"""Coordinated runtime ownership, generation leases, and model release.

The service is intentionally independent of ComfyUI and of any particular HTTP
client implementation.  Router operations are supplied through a small protocol
so the typed llama-server client can be integrated without a circular import.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from .process import OwnedProcessController, StopResult

LOGGER = logging.getLogger(__name__)


class RuntimeMode(str, Enum):
    NONE = "none"
    DIRECT = "direct"
    ROUTER = "router"
    ATTACHED = "attached"


class RuntimeLifecycle(str, Enum):
    IDLE = "idle"
    READY = "ready"
    GENERATING = "generating"
    RELEASE_PENDING = "release_pending"
    RELEASING = "releasing"
    ERROR = "error"


class ReleaseStatus(str, Enum):
    NOOP = "noop"
    DEFERRED = "deferred"
    COALESCED = "coalesced"
    COMPLETE = "complete"
    FALLBACK_COMPLETE = "fallback_complete"
    FAILED = "failed"


class RuntimeReleasePending(RuntimeError):
    """Raised when a new generation attempts to start during release."""


@dataclass(frozen=True)
class RouterReleaseModel:
    """Minimal normalized router model state used by release coordination."""

    model_id: str
    state: str
    raw: Any = None


@runtime_checkable
class RouterReleaseClient(Protocol):
    """Dependency-injected barrier contract for router model release."""

    def list_models(self) -> Sequence[Any]:
        """Return current router models and their residency states."""

    def unload_model(self, model_id: str, *, timeout: float | None = None) -> Any:
        """Unload the exact model and return only after its terminal barrier."""


@dataclass(frozen=True)
class ReleaseResult:
    request_id: str
    source: str
    status: ReleaseStatus
    mode: RuntimeMode
    owned: bool
    started_at: float
    completed_at: float | None = None
    released_models: tuple[str, ...] = ()
    coalesced: bool = False
    fallback_used: bool = False
    stop_result: StopResult | None = None
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.status in {
            ReleaseStatus.NOOP,
            ReleaseStatus.COMPLETE,
            ReleaseStatus.FALLBACK_COMPLETE,
        }

    @property
    def terminal(self) -> bool:
        return self.status not in {
            ReleaseStatus.DEFERRED,
            ReleaseStatus.COALESCED,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "source": self.source,
            "status": self.status.value,
            "mode": self.mode.value,
            "owned": self.owned,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "released_models": list(self.released_models),
            "coalesced": self.coalesced,
            "fallback_used": self.fallback_used,
            "stop_result": self.stop_result.as_dict() if self.stop_result else None,
            "error": self.error,
            "success": self.success,
            "terminal": self.terminal,
        }


_RESIDENT_ROUTER_STATES = frozenset(
    {
        "loaded",
        "loading",
        "sleeping",
        "unloading",
        "starting",
        "ready",
    }
)
_TERMINAL_UNLOADED_STATES = frozenset({"unloaded", "not_loaded"})


class RuntimeService:
    """Single coordination point for all owned runtime lifecycle operations."""

    def __init__(
        self,
        process_controller: OwnedProcessController | None = None,
        *,
        router_release_timeout: float = 60.0,
    ) -> None:
        if router_release_timeout <= 0:
            raise ValueError("router_release_timeout must be positive")
        self._process = process_controller or OwnedProcessController()
        self._router_release_timeout = router_release_timeout

        self._state_lock = threading.RLock()
        self._condition = threading.Condition(self._state_lock)
        self._operation_lock = threading.RLock()
        self._mode = RuntimeMode.NONE
        self._owned = False
        self._router_client: RouterReleaseClient | None = None
        self._lifecycle = RuntimeLifecycle.IDLE
        self._active_generations = 0
        self._pending_release = False
        self._pending_sources: set[str] = set()
        self._release_in_progress = False
        self._release_generation = 0
        self._last_release: ReleaseResult | None = None
        self._last_error: str | None = None
        self._listeners: list[Callable[[ReleaseResult], None]] = []

    @property
    def process_controller(self) -> OwnedProcessController:
        return self._process

    @property
    def mode(self) -> RuntimeMode:
        with self._state_lock:
            return self._mode

    @property
    def is_owned(self) -> bool:
        with self._state_lock:
            return self._owned

    @property
    def active_generations(self) -> int:
        with self._state_lock:
            return self._active_generations

    @property
    def release_pending(self) -> bool:
        with self._state_lock:
            return self._pending_release

    def configure_direct_owned(self) -> None:
        self._configure(RuntimeMode.DIRECT, owned=True, router_client=None)

    def configure_router_owned(self, router_client: RouterReleaseClient) -> None:
        if router_client is None:
            raise ValueError("router_client is required for owned router mode")
        self._configure(RuntimeMode.ROUTER, owned=True, router_client=router_client)

    def configure_attached(self) -> None:
        """Mark the active endpoint as externally owned and therefore immutable."""

        self._configure(RuntimeMode.ATTACHED, owned=False, router_client=None)

    def clear_runtime(self) -> None:
        self._configure(RuntimeMode.NONE, owned=False, router_client=None)

    def _configure(
        self,
        mode: RuntimeMode,
        *,
        owned: bool,
        router_client: RouterReleaseClient | None,
    ) -> None:
        with self._operation_lock, self._state_lock:
            if self._active_generations:
                raise RuntimeError("cannot reconfigure runtime during active generation")
            if self._release_in_progress:
                raise RuntimeError("cannot reconfigure runtime during release")
            if mode in {RuntimeMode.NONE, RuntimeMode.ATTACHED} and self._process.has_owned_process:
                raise RuntimeError(
                    "cannot discard owned process state; release the owned runtime first"
                )
            self._mode = mode
            self._owned = owned
            self._router_client = router_client
            self._pending_release = False
            self._pending_sources.clear()
            self._lifecycle = (
                RuntimeLifecycle.READY if mode != RuntimeMode.NONE else RuntimeLifecycle.IDLE
            )
            self._last_error = None

    @contextlib.contextmanager
    def serialized_operation(self) -> Iterator[None]:
        """Serialize start, stop, reconfigure, and router mutations."""

        with self._operation_lock:
            yield

    @contextlib.contextmanager
    def generation_lease(self) -> Iterator[None]:
        """Prevent implicit release while one generation request is active."""

        with self._state_lock:
            if self._release_in_progress or self._pending_release:
                raise RuntimeReleasePending("runtime release is pending or in progress")
            self._active_generations += 1
            self._lifecycle = RuntimeLifecycle.GENERATING

        try:
            yield
        finally:
            should_release = False
            source = "deferred"
            with self._state_lock:
                self._active_generations = max(0, self._active_generations - 1)
                if self._active_generations == 0:
                    if self._pending_release and not self._release_in_progress:
                        should_release = True
                        source = "+".join(sorted(self._pending_sources)) or "deferred"
                        self._begin_release_locked()
                    else:
                        self._lifecycle = (
                            RuntimeLifecycle.READY
                            if self._mode != RuntimeMode.NONE
                            else RuntimeLifecycle.IDLE
                        )
                self._condition.notify_all()

            if should_release:
                self._execute_release(source=source)

    def add_release_listener(self, listener: Callable[[ReleaseResult], None]) -> None:
        with self._state_lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def remove_release_listener(self, listener: Callable[[ReleaseResult], None]) -> None:
        with self._state_lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def request_release(
        self,
        *,
        source: str = "explicit",
        wait_for_coalesced: bool = True,
    ) -> ReleaseResult:
        """Release all implicitly manageable resources.

        Attached endpoints are immutable.  If generation is active the request is
        recorded and the final lease performs release.  Concurrent callers share
        the same release operation.
        """

        request_id = uuid.uuid4().hex
        started_at = time.time()
        source = source or "unknown"

        noop_result: ReleaseResult | None = None
        noop_listeners: tuple[Callable[[ReleaseResult], None], ...] = ()
        with self._state_lock:
            mode = self._mode
            owned = self._owned
            if not owned or mode in {RuntimeMode.NONE, RuntimeMode.ATTACHED}:
                noop_result = ReleaseResult(
                    request_id=request_id,
                    source=source,
                    status=ReleaseStatus.NOOP,
                    mode=mode,
                    owned=owned,
                    started_at=started_at,
                    completed_at=time.time(),
                )
                self._last_release = noop_result
                noop_listeners = tuple(self._listeners)

            if noop_result is not None:
                # Return after emitting below, outside the state lock.
                pass

            elif self._active_generations > 0:
                self._pending_release = True
                self._pending_sources.add(source)
                self._lifecycle = RuntimeLifecycle.RELEASE_PENDING
                result = ReleaseResult(
                    request_id=request_id,
                    source=source,
                    status=ReleaseStatus.DEFERRED,
                    mode=mode,
                    owned=owned,
                    started_at=started_at,
                )
                self._last_release = result
                return result

            elif self._release_in_progress:
                observed_generation = self._release_generation
                if not wait_for_coalesced:
                    return ReleaseResult(
                        request_id=request_id,
                        source=source,
                        status=ReleaseStatus.COALESCED,
                        mode=mode,
                        owned=owned,
                        started_at=started_at,
                        coalesced=True,
                    )
                while self._release_in_progress and self._release_generation == observed_generation:
                    self._condition.wait()
                if self._last_release is not None:
                    return replace(
                        self._last_release,
                        request_id=request_id,
                        source=source,
                        coalesced=True,
                    )
                # A release operation always records its terminal result before it
                # notifies waiters.  Preserve a deterministic failure if a future
                # implementation violates that invariant rather than running an
                # uncoordinated second release.
                return ReleaseResult(
                    request_id=request_id,
                    source=source,
                    status=ReleaseStatus.FAILED,
                    mode=mode,
                    owned=owned,
                    started_at=started_at,
                    completed_at=time.time(),
                    coalesced=True,
                    error="coalesced release completed without a terminal result",
                )

            else:
                self._begin_release_locked()

        if noop_result is not None:
            for listener in noop_listeners:
                try:
                    listener(noop_result)
                except Exception as exc:
                    LOGGER.warning(
                        "runtime release listener failed (%s)",
                        type(exc).__name__,
                    )
            return noop_result

        return self._execute_release(source=source, request_id=request_id, started_at=started_at)

    def _begin_release_locked(self) -> None:
        self._release_in_progress = True
        self._release_generation += 1
        self._pending_release = False
        self._pending_sources.clear()
        self._lifecycle = RuntimeLifecycle.RELEASING

    def _execute_release(
        self,
        *,
        source: str,
        request_id: str | None = None,
        started_at: float | None = None,
    ) -> ReleaseResult:
        request_id = request_id or uuid.uuid4().hex
        started_at = started_at or time.time()
        with self._operation_lock:
            with self._state_lock:
                mode = self._mode
                owned = self._owned
                router_client = self._router_client

            try:
                if not owned or mode in {RuntimeMode.NONE, RuntimeMode.ATTACHED}:
                    result = ReleaseResult(
                        request_id=request_id,
                        source=source,
                        status=ReleaseStatus.NOOP,
                        mode=mode,
                        owned=owned,
                        started_at=started_at,
                        completed_at=time.time(),
                    )
                elif mode == RuntimeMode.DIRECT:
                    result = self._release_direct(request_id, source, mode, started_at)
                elif mode == RuntimeMode.ROUTER:
                    result = self._release_router(
                        request_id,
                        source,
                        mode,
                        started_at,
                        router_client,
                    )
                else:  # defensive for future enum members
                    raise RuntimeError(f"unsupported runtime mode: {mode.value}")
            except Exception as exc:
                result = ReleaseResult(
                    request_id=request_id,
                    source=source,
                    status=ReleaseStatus.FAILED,
                    mode=mode,
                    owned=owned,
                    started_at=started_at,
                    completed_at=time.time(),
                    error=f"{type(exc).__name__}: {exc}",
                )

        self._finish_release(result)
        return result

    def _release_direct(
        self,
        request_id: str,
        source: str,
        mode: RuntimeMode,
        started_at: float,
    ) -> ReleaseResult:
        if not self._process.has_owned_process:
            return ReleaseResult(
                request_id=request_id,
                source=source,
                status=ReleaseStatus.NOOP,
                mode=mode,
                owned=True,
                started_at=started_at,
                completed_at=time.time(),
            )
        stop_result = self._process.stop()
        return ReleaseResult(
            request_id=request_id,
            source=source,
            status=ReleaseStatus.COMPLETE if stop_result.complete else ReleaseStatus.FAILED,
            mode=mode,
            owned=True,
            started_at=started_at,
            completed_at=time.time(),
            stop_result=stop_result,
            error=stop_result.error if not stop_result.complete else None,
        )

    def _release_router(
        self,
        request_id: str,
        source: str,
        mode: RuntimeMode,
        started_at: float,
        client: RouterReleaseClient | None,
    ) -> ReleaseResult:
        if client is None:
            return self._router_fallback(
                request_id,
                source,
                mode,
                started_at,
                (),
                "router release client is unavailable",
            )

        released: list[str] = []
        try:
            models = tuple(self._normalize_router_model(item) for item in client.list_models())
            model_ids = tuple(model.model_id for model in models)
            if len(model_ids) != len(set(model_ids)):
                raise ValueError("router returned duplicate exact model identities")
            if any(model.state == "unknown" for model in models):
                raise RuntimeError("router returned an unknown residency state")
            targets = tuple(model for model in models if model.state in _RESIDENT_ROUTER_STATES)
            for model in targets:
                terminal = client.unload_model(
                    model.model_id,
                    timeout=self._router_release_timeout,
                )
                # The shared typed client returns ModelOperationResult.  Minimal
                # injected test/adaptor clients may return the terminal model
                # directly instead.
                terminal = getattr(terminal, "model", terminal)
                normalized = self._normalize_router_model(terminal, fallback_id=model.model_id)
                if normalized.model_id != model.model_id:
                    raise RuntimeError(
                        f"router barrier returned {normalized.model_id!r} for "
                        f"requested model {model.model_id!r}"
                    )
                if normalized.state not in _TERMINAL_UNLOADED_STATES:
                    raise RuntimeError(
                        f"model {model.model_id!r} did not reach terminal unloaded state"
                    )
                released.append(model.model_id)
        except Exception as exc:
            return self._router_fallback(
                request_id,
                source,
                mode,
                started_at,
                tuple(released),
                f"router barrier failed ({type(exc).__name__}: {exc})",
            )

        return ReleaseResult(
            request_id=request_id,
            source=source,
            status=ReleaseStatus.COMPLETE,
            mode=mode,
            owned=True,
            started_at=started_at,
            completed_at=time.time(),
            released_models=tuple(released),
        )

    def _router_fallback(
        self,
        request_id: str,
        source: str,
        mode: RuntimeMode,
        started_at: float,
        released_models: tuple[str, ...],
        reason: str,
    ) -> ReleaseResult:
        if not self._process.has_owned_process:
            return ReleaseResult(
                request_id=request_id,
                source=source,
                status=ReleaseStatus.FAILED,
                mode=mode,
                owned=True,
                started_at=started_at,
                completed_at=time.time(),
                released_models=released_models,
                fallback_used=True,
                error=reason,
            )
        stop_result = self._process.stop()
        status = ReleaseStatus.FALLBACK_COMPLETE if stop_result.complete else ReleaseStatus.FAILED
        return ReleaseResult(
            request_id=request_id,
            source=source,
            status=status,
            mode=mode,
            owned=True,
            started_at=started_at,
            completed_at=time.time(),
            released_models=released_models,
            fallback_used=True,
            stop_result=stop_result,
            error=None if stop_result.complete else (stop_result.error or reason),
        )

    def _finish_release(self, result: ReleaseResult) -> None:
        with self._state_lock:
            self._last_release = result
            self._release_in_progress = False
            if result.success:
                self._lifecycle = (
                    RuntimeLifecycle.READY
                    if self._mode == RuntimeMode.ROUTER and self._process.is_running
                    else RuntimeLifecycle.IDLE
                )
                self._last_error = None
                direct_release_complete = result.mode == RuntimeMode.DIRECT and result.status in {
                    ReleaseStatus.NOOP,
                    ReleaseStatus.COMPLETE,
                }
                owned_process_stopped = (
                    result.stop_result is not None and result.stop_result.complete
                )
                if direct_release_complete or owned_process_stopped:
                    self._mode = RuntimeMode.NONE
                    self._owned = False
                    self._router_client = None
            else:
                self._lifecycle = RuntimeLifecycle.ERROR
                self._last_error = result.error
            listeners = tuple(self._listeners)
            self._condition.notify_all()

        for listener in listeners:
            try:
                listener(result)
            except Exception as exc:
                LOGGER.warning(
                    "runtime release listener failed (%s)",
                    type(exc).__name__,
                )

    @classmethod
    def _normalize_router_model(
        cls,
        item: Any,
        *,
        fallback_id: str | None = None,
    ) -> RouterReleaseModel:
        if isinstance(item, RouterReleaseModel):
            return item

        model_id = cls._extract_value(item, ("model_id", "id", "model", "name", "alias"))
        if model_id is None:
            model_id = fallback_id
        if not model_id:
            raise ValueError("router model record has no exact identity")

        state = cls._extract_state(item)
        return RouterReleaseModel(str(model_id), state, raw=item)

    @staticmethod
    def _extract_value(item: Any, keys: Sequence[str]) -> Any:
        if isinstance(item, dict):
            for key in keys:
                value = item.get(key)
                if value is not None:
                    return value
            return None
        for key in keys:
            value = getattr(item, key, None)
            if value is not None:
                return value
        return None

    @classmethod
    def _extract_state(cls, item: Any) -> str:
        value = cls._extract_value(item, ("normalized_state", "state", "status"))
        depth = 0
        while depth < 4:
            if isinstance(value, str):
                return value.strip().lower().replace("-", "_").replace(" ", "_")
            if isinstance(value, dict):
                value = cls._extract_value(value, ("state", "status", "value", "phase"))
                depth += 1
                continue
            nested = cls._extract_value(value, ("state", "status", "value", "phase"))
            if nested is value or nested is None:
                break
            value = nested
            depth += 1
        return "unknown"

    def diagnostics(self) -> dict[str, Any]:
        with self._operation_lock:
            with self._state_lock:
                last_release = self._last_release
                data = {
                    "mode": self._mode.value,
                    "owned": self._owned,
                    "lifecycle": self._lifecycle.value,
                    "active_generations": self._active_generations,
                    "release_pending": self._pending_release,
                    "release_in_progress": self._release_in_progress,
                    "last_error": self._last_error,
                    "last_release": last_release.as_dict() if last_release else None,
                }
            data["process"] = self._process.snapshot().as_dict()
        return data


_DEFAULT_SERVICE: RuntimeService | None = None
_DEFAULT_SERVICE_LOCK = threading.Lock()


def get_runtime_service() -> RuntimeService:
    """Return the process-wide runtime coordinator without importing ComfyUI."""

    global _DEFAULT_SERVICE
    if _DEFAULT_SERVICE is None:
        with _DEFAULT_SERVICE_LOCK:
            if _DEFAULT_SERVICE is None:
                _DEFAULT_SERVICE = RuntimeService()
    return _DEFAULT_SERVICE


__all__ = [
    "ReleaseResult",
    "ReleaseStatus",
    "RouterReleaseModel",
    "RouterReleaseClient",
    "RuntimeLifecycle",
    "RuntimeMode",
    "RuntimeReleasePending",
    "RuntimeService",
    "get_runtime_service",
]
