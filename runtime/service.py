"""Coordinated runtime ownership, generation leases, and model release.

The service is intentionally independent of ComfyUI and of any particular HTTP
client implementation.  Router operations are supplied through a small protocol
so the typed llama-server client can be integrated without a circular import.
"""

from __future__ import annotations

import contextlib
import logging
import math
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from .process import OwnedProcessController, StopResult

LOGGER = logging.getLogger(__name__)

_MAX_CANONICAL_MODEL_ID_BYTES = 4_096


def _canonical_generation_model_id(value: object) -> str:
    """Validate one router-resolved identity before it gains lease authority."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError("router resolver returned no exact model ID")
    normalized = value.strip()
    if len(normalized) > _MAX_CANONICAL_MODEL_ID_BYTES:
        raise ValueError("router resolver returned an oversized exact model ID")
    try:
        encoded = normalized.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise ValueError("router resolver returned an invalid exact model ID") from None
    if len(encoded) > _MAX_CANONICAL_MODEL_ID_BYTES:
        raise ValueError("router resolver returned an oversized exact model ID")
    return normalized


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


class ReleaseScope(str, Enum):
    """Authority boundary for one release operation."""

    GLOBAL = "global"
    DIRECT_RUNTIME = "direct_runtime"
    ROUTER_MODEL = "router_model"
    ATTACHED = "attached"


class RuntimeReleasePending(RuntimeError):
    """Raised when a new generation attempts to start during release."""


class RuntimeOperationBusy(RuntimeError):
    """Raised before an explicit runtime mutation while generation is active."""

    def __init__(self, operation: str, active_generations: int) -> None:
        self.operation = operation
        self.active_generations = active_generations
        if active_generations:
            reason = f"{active_generations} generation request(s) are active"
        else:
            reason = "a native runtime release is pending or in progress"
        super().__init__(f"cannot {operation}: {reason}")


class ReleaseWaitTimeout(TimeoutError):
    """Raised when one caller stops waiting for accepted cleanup."""


class ReleaseWaitCancelled(RuntimeError):
    """Raised when one caller cancels its wait, not the cleanup operation."""


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
    released_model_states: tuple[tuple[str, str], ...] = ()
    released_model_diagnostics: tuple[Mapping[str, Any], ...] = ()
    coalesced: bool = False
    fallback_used: bool = False
    stop_result: StopResult | None = None
    error: str | None = None
    scope: ReleaseScope = ReleaseScope.GLOBAL
    operation_id: str | None = None
    target_model: str | None = None
    runtime_epoch: int | None = None
    superseded_by: str | None = None
    driver_memory_verified: bool = False

    @property
    def success(self) -> bool:
        """Whether the release request was accepted without an operational failure."""

        return self.status != ReleaseStatus.FAILED

    @property
    def accepted(self) -> bool:
        return self.success

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
            "released_model_states": [
                {"id": model_id, "state": state} for model_id, state in self.released_model_states
            ],
            "released_model_diagnostics": [
                {
                    "id": item.get("id"),
                    "state": item.get("state"),
                    # Preserve the established response shape without exposing
                    # arbitrary records returned by the upstream router.
                    "raw": {},
                }
                for item in self.released_model_diagnostics
            ],
            "coalesced": self.coalesced,
            "fallback_used": self.fallback_used,
            "stop_result": self.stop_result.as_dict() if self.stop_result else None,
            "error": self.error,
            "scope": self.scope.value,
            "operation_id": self.operation_id,
            "target_model": self.target_model,
            "runtime_epoch": self.runtime_epoch,
            "superseded_by": self.superseded_by,
            "driver_memory_verified": self.driver_memory_verified,
            "success": self.success,
            "accepted": self.accepted,
            "terminal": self.terminal,
        }


@dataclass(frozen=True)
class ReleaseTarget:
    """Immutable resource identity captured before a generation begins."""

    scope: ReleaseScope
    runtime_epoch: int
    model_id: str | None = None

    def __post_init__(self) -> None:
        if self.runtime_epoch < 0:
            raise ValueError("runtime_epoch must be non-negative")
        if self.scope == ReleaseScope.ROUTER_MODEL:
            if not isinstance(self.model_id, str) or not self.model_id.strip():
                raise ValueError("router-model release requires an exact model ID")
            object.__setattr__(self, "model_id", self.model_id.strip())
        elif self.model_id is not None:
            raise ValueError("only router-model release targets accept model_id")


class _ScopedReleaseState(str, Enum):
    DORMANT = "dormant"
    ARMED = "armed"
    QUEUED = "queued"
    RUNNING = "running"
    SUPERSEDED = "superseded"
    TRANSITIONING = "transitioning"
    TERMINAL = "terminal"


@dataclass
class _ScopedReleaseOperation:
    operation_id: str
    target: ReleaseTarget
    mode: RuntimeMode
    owned: bool
    started_at: float
    sources: set[str]
    state: _ScopedReleaseState = _ScopedReleaseState.DORMANT
    superseded_by: str | None = None
    result: ReleaseResult | None = None
    event: threading.Event = field(default_factory=threading.Event)

    @property
    def key(self) -> tuple[int, ReleaseScope, str | None]:
        return (self.target.runtime_epoch, self.target.scope, self.target.model_id)


class ReleaseHandle:
    """Awaitable receipt for scoped cleanup.

    Timeout and cancellation abandon only this wait.  The coordinator-owned
    operation deliberately has no public cancellation method.
    """

    __slots__ = (
        "_coalesced",
        "_operation",
        "_request_id",
        "_source",
    )

    def __init__(
        self,
        operation: _ScopedReleaseOperation,
        *,
        request_id: str,
        source: str,
        coalesced: bool,
    ) -> None:
        self._operation = operation
        self._request_id = request_id
        self._source = source
        self._coalesced = coalesced

    @property
    def request_id(self) -> str:
        return self._request_id

    @property
    def operation_id(self) -> str:
        return self._operation.operation_id

    @property
    def target(self) -> ReleaseTarget:
        return self._operation.target

    @property
    def coalesced(self) -> bool:
        return self._coalesced

    @property
    def done(self) -> bool:
        return self._operation.event.is_set()

    def wait(
        self,
        *,
        timeout: float | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> ReleaseResult:
        if timeout is not None and (timeout < 0 or not math.isfinite(timeout)):
            raise ValueError("release wait timeout must be non-negative or None")
        deadline = None if timeout is None else time.monotonic() + timeout

        while not self._operation.event.is_set():
            if cancel is not None and cancel():
                raise ReleaseWaitCancelled(
                    f"release wait cancelled; operation {self.operation_id} continues"
                )
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise ReleaseWaitTimeout(
                    f"release wait timed out; operation {self.operation_id} continues"
                )
            self._operation.event.wait(timeout=0.05 if remaining is None else min(0.05, remaining))

        result = self._operation.result
        if result is None:  # pragma: no cover - defensive invariant
            raise RuntimeError("release operation completed without a terminal result")
        return replace(
            result,
            request_id=self.request_id,
            source=self._source,
            coalesced=self.coalesced,
            operation_id=self.operation_id,
        )


@dataclass(frozen=True)
class GenerationLeaseToken:
    """Exact generation admission recorded by the runtime coordinator."""

    lease_id: str
    runtime_epoch: int
    mode: RuntimeMode
    owned: bool
    model_id: str | None = None
    release_handle: ReleaseHandle | None = field(default=None, repr=False, compare=False)


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
_TERMINAL_UNLOADED_STATES = frozenset({"unloaded", "not_loaded", "failed"})
_TERMINAL_NONRESIDENT_STATES = frozenset({"unloaded", "not_loaded", "failed", "downloaded"})
_MAX_REENTRANT_LISTENER_NOTIFICATIONS = 1024


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
        # Lifecycle mutation I/O is serialized separately from short admission
        # bookkeeping.  A scoped router unload may therefore release model A
        # without preventing an exact model-B lease from being admitted.
        self._runtime_mutation_lock = threading.RLock()
        self._mode = RuntimeMode.NONE
        self._owned = False
        self._router_client: RouterReleaseClient | None = None
        self._runtime_epoch = 0
        self._lifecycle = RuntimeLifecycle.IDLE
        self._active_generations = 0
        self._active_direct_generations = 0
        self._active_router_generations: dict[str, int] = {}
        self._active_unknown_router_generations = 0
        self._leases: dict[str, GenerationLeaseToken] = {}
        self._pending_release = False
        self._pending_sources: set[str] = set()
        self._pending_global_operation_id: str | None = None
        self._current_global_operation_id: str | None = None
        self._release_in_progress = False
        self._release_generation = 0
        self._last_release_generation = 0
        self._last_release: ReleaseResult | None = None
        self._last_scoped_release: ReleaseResult | None = None
        self._scoped_operations: dict[
            tuple[int, ReleaseScope, str | None], _ScopedReleaseOperation
        ] = {}
        self._scoped_queue: deque[_ScopedReleaseOperation] = deque()
        self._scoped_worker: threading.Thread | None = None
        self._last_error: str | None = None
        self._listeners: list[Callable[[ReleaseResult], None]] = []
        self._listener_delivery = threading.local()

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
    def runtime_epoch(self) -> int:
        with self._state_lock:
            return self._runtime_epoch

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
        with self._operation_lock, self._runtime_mutation_lock, self._state_lock:
            if self._active_generations:
                raise RuntimeError("cannot reconfigure runtime during active generation")
            if self._release_in_progress:
                raise RuntimeError("cannot reconfigure runtime during release")
            if self._pending_release:
                raise RuntimeError("cannot reconfigure runtime while release is pending")
            if self._has_active_scoped_operations_locked():
                raise RuntimeError("cannot reconfigure runtime during scoped release")
            if mode in {RuntimeMode.NONE, RuntimeMode.ATTACHED} and self._process.has_owned_process:
                raise RuntimeError(
                    "cannot discard owned process state; release the owned runtime first"
                )
            self._runtime_epoch += 1
            self._mode = mode
            self._owned = owned
            self._router_client = router_client
            self._pending_release = False
            self._pending_sources.clear()
            self._pending_global_operation_id = None
            self._current_global_operation_id = None
            self._lifecycle = (
                RuntimeLifecycle.READY if mode != RuntimeMode.NONE else RuntimeLifecycle.IDLE
            )
            self._last_error = None

    @contextlib.contextmanager
    def serialized_operation(
        self,
        *,
        require_idle: bool = False,
        operation: str = "mutate the runtime",
    ) -> Iterator[None]:
        """Serialize runtime mutations and optionally reject them before generation."""

        if require_idle:
            with self._state_lock:
                self._raise_if_runtime_busy_locked(operation)
        with self._operation_lock, self._runtime_mutation_lock:
            if require_idle:
                with self._state_lock:
                    self._raise_if_runtime_busy_locked(operation)
            yield

    def _raise_if_runtime_busy_locked(self, operation: str) -> None:
        if (
            self._active_generations
            or self._pending_release
            or self._release_in_progress
            or self._has_active_scoped_operations_locked()
        ):
            raise RuntimeOperationBusy(operation, self._active_generations)

    def begin_generation(
        self,
        *,
        model_id: str | None = None,
        release_after: bool = False,
        source: str = "generation",
    ) -> GenerationLeaseToken:
        """Admit one exact generation without retaining the operation lock."""

        source = source or "generation"
        with self._operation_lock, self._state_lock:
            normalized_model = model_id.strip() if isinstance(model_id, str) else None
            normalized_model = normalized_model or None
            return self._begin_generation_locked(
                normalized_model,
                release_after=release_after,
                source=source,
            )

    def resolve_and_begin_generation(
        self,
        *,
        requested_model: str | None,
        resolve_router_model: Callable[[str], str],
        release_after: bool = False,
        source: str = "generation",
    ) -> tuple[str | None, GenerationLeaseToken]:
        """Resolve an owned router target and admit its lease atomically.

        The resolver runs without the state lock because it may perform router
        I/O, while the shared operation lock prevents lifecycle mutation.  The
        epoch/mode recheck rejects reentrant or otherwise stale resolution.  A
        direct runtime returns the normalized request unchanged and never calls
        the router resolver.
        """

        source = source or "generation"
        normalized_request = requested_model.strip() if isinstance(requested_model, str) else None
        normalized_request = normalized_request or None

        with self._operation_lock:
            with self._state_lock:
                mode = self._mode
                owned = self._owned
                runtime_epoch = self._runtime_epoch
                if not owned or mode not in {RuntimeMode.DIRECT, RuntimeMode.ROUTER}:
                    raise RuntimeError(
                        "managed generation requires an owned direct or router runtime"
                    )
                if self._pending_release or self._release_in_progress:
                    raise RuntimeReleasePending("runtime release is pending or in progress")
                if mode == RuntimeMode.ROUTER and normalized_request is None:
                    raise ValueError("router generation requires an exact model selection")

            resolved_model = normalized_request
            if mode == RuntimeMode.ROUTER:
                if not callable(resolve_router_model):
                    raise TypeError("resolve_router_model must be callable")
                resolved = resolve_router_model(normalized_request)
                resolved_model = _canonical_generation_model_id(resolved)

            with self._state_lock:
                if (
                    self._runtime_epoch != runtime_epoch
                    or self._mode != mode
                    or self._owned != owned
                ):
                    raise RuntimeError("runtime changed during exact model resolution")
                lease = self._begin_generation_locked(
                    resolved_model if mode == RuntimeMode.ROUTER else None,
                    release_after=release_after,
                    source=source,
                )
                return resolved_model, lease

    def _begin_generation_locked(
        self,
        model_id: str | None,
        *,
        release_after: bool,
        source: str,
    ) -> GenerationLeaseToken:
        self._assert_generation_admission_locked(model_id)

        mode = self._mode
        owned = self._owned
        handle: ReleaseHandle | None = None
        if release_after:
            if not owned or mode not in {RuntimeMode.DIRECT, RuntimeMode.ROUTER}:
                raise RuntimeError(
                    "release-after-generation requires an owned direct or router runtime"
                )
            if mode == RuntimeMode.ROUTER and model_id is None:
                raise ValueError("router release-after-generation requires an exact model ID")
            target = ReleaseTarget(
                ReleaseScope.DIRECT_RUNTIME
                if mode == RuntimeMode.DIRECT
                else ReleaseScope.ROUTER_MODEL,
                self._runtime_epoch,
                model_id if mode == RuntimeMode.ROUTER else None,
            )
            key = (target.runtime_epoch, target.scope, target.model_id)
            operation = self._scoped_operations.get(key)
            coalesced = operation is not None
            if operation is None:
                operation = _ScopedReleaseOperation(
                    operation_id=uuid.uuid4().hex,
                    target=target,
                    mode=mode,
                    owned=owned,
                    started_at=time.time(),
                    sources={source},
                )
                self._scoped_operations[key] = operation
            else:
                operation.sources.add(source)
            handle = ReleaseHandle(
                operation,
                request_id=uuid.uuid4().hex,
                source=source,
                coalesced=coalesced,
            )

        token = GenerationLeaseToken(
            lease_id=uuid.uuid4().hex,
            runtime_epoch=self._runtime_epoch,
            mode=mode,
            owned=owned,
            model_id=model_id if mode == RuntimeMode.ROUTER else None,
            release_handle=handle,
        )
        self._leases[token.lease_id] = token
        self._active_generations += 1
        if mode == RuntimeMode.DIRECT:
            self._active_direct_generations += 1
        elif mode == RuntimeMode.ROUTER:
            if token.model_id is None:
                self._active_unknown_router_generations += 1
            else:
                self._active_router_generations[token.model_id] = (
                    self._active_router_generations.get(token.model_id, 0) + 1
                )
        self._refresh_lifecycle_locked()
        return token

    def finish_generation(self, lease: GenerationLeaseToken) -> ReleaseHandle | None:
        """Exit one lease, arm its cleanup, and return the pre-created handle."""

        should_release_global = False
        with self._state_lock:
            active = self._leases.get(lease.lease_id)
            if active is None:
                raise RuntimeError("generation lease is already finished or unknown")
            if active is not lease:
                raise RuntimeError("generation lease token does not match the active lease")
            self._leases.pop(lease.lease_id)
            self._active_generations = max(0, self._active_generations - 1)
            if lease.mode == RuntimeMode.DIRECT:
                self._active_direct_generations = max(0, self._active_direct_generations - 1)
            elif lease.mode == RuntimeMode.ROUTER:
                if lease.model_id is None:
                    self._active_unknown_router_generations = max(
                        0, self._active_unknown_router_generations - 1
                    )
                else:
                    remaining = self._active_router_generations.get(lease.model_id, 0) - 1
                    if remaining > 0:
                        self._active_router_generations[lease.model_id] = remaining
                    else:
                        self._active_router_generations.pop(lease.model_id, None)

            handle = lease.release_handle
            if handle is not None:
                operation = handle._operation
                if operation.state == _ScopedReleaseState.DORMANT:
                    operation.started_at = time.time()
                if lease.runtime_epoch != self._runtime_epoch:
                    self._fail_scoped_operation_locked(
                        operation,
                        "runtime epoch changed before scoped release armed",
                    )
                elif self._pending_release or self._release_in_progress:
                    global_id = (
                        self._pending_global_operation_id
                        or self._current_global_operation_id
                        or "global_release"
                    )
                    self._supersede_one_scoped_locked(operation, global_id)
                elif operation.state == _ScopedReleaseState.DORMANT:
                    operation.state = _ScopedReleaseState.ARMED

            self._queue_ready_scoped_operations_locked()
            should_release_global = (
                self._active_generations == 0
                and self._pending_release
                and not self._release_in_progress
            )
            self._refresh_lifecycle_locked()
            self._condition.notify_all()

        if should_release_global:
            self.request_release(source="deferred")
        return lease.release_handle

    def _assert_generation_admission_locked(self, model_id: str | None) -> None:
        if self._release_in_progress or self._pending_release:
            raise RuntimeReleasePending("runtime release is pending or in progress")

        active_scoped = tuple(
            operation
            for operation in self._scoped_operations.values()
            if operation.state not in {_ScopedReleaseState.DORMANT, _ScopedReleaseState.TERMINAL}
        )
        if self._mode == RuntimeMode.DIRECT and any(
            operation.target.scope == ReleaseScope.DIRECT_RUNTIME for operation in active_scoped
        ):
            raise RuntimeReleasePending("direct runtime release is pending or in progress")
        if self._mode == RuntimeMode.ROUTER:
            router_operations = tuple(
                operation
                for operation in active_scoped
                if operation.target.scope == ReleaseScope.ROUTER_MODEL
            )
            if model_id is None and router_operations:
                raise RuntimeReleasePending(
                    "an exact router-model release is pending; model identity is required"
                )
            if any(operation.target.model_id == model_id for operation in router_operations):
                raise RuntimeReleasePending(
                    f"release for router model {model_id!r} is pending or in progress"
                )

    @contextlib.contextmanager
    def generation_lease(self) -> Iterator[None]:
        """Prevent implicit release while one generation request is active."""

        lease = self.begin_generation()
        try:
            yield
        finally:
            self.finish_generation(lease)

    def _has_active_scoped_operations_locked(self) -> bool:
        return any(
            operation.state != _ScopedReleaseState.TERMINAL
            for operation in self._scoped_operations.values()
        )

    def _scoped_operation_ready_locked(self, operation: _ScopedReleaseOperation) -> bool:
        if operation.target.scope == ReleaseScope.DIRECT_RUNTIME:
            return self._active_direct_generations == 0
        if operation.target.scope == ReleaseScope.ROUTER_MODEL:
            target = operation.target.model_id
            return (
                target is not None
                and self._active_router_generations.get(target, 0) == 0
                and self._active_unknown_router_generations == 0
            )
        return False

    def _queue_ready_scoped_operations_locked(self) -> None:
        if self._pending_release or self._release_in_progress:
            return
        for operation in tuple(self._scoped_operations.values()):
            if operation.state == _ScopedReleaseState.ARMED and self._scoped_operation_ready_locked(
                operation
            ):
                operation.state = _ScopedReleaseState.QUEUED
                self._scoped_queue.append(operation)
        if self._scoped_queue and self._scoped_worker is None:
            worker = threading.Thread(
                target=self._scoped_worker_main,
                name="llamacpp-scoped-release",
                daemon=True,
            )
            self._scoped_worker = worker
            worker.start()

    def _scoped_worker_main(self) -> None:
        """Drain ready operations with at most one service-owned worker."""

        while True:
            with self._state_lock:
                operation: _ScopedReleaseOperation | None = None
                while self._scoped_queue:
                    candidate = self._scoped_queue.popleft()
                    if candidate.state == _ScopedReleaseState.QUEUED:
                        operation = candidate
                        break
                if operation is None:
                    self._scoped_worker = None
                    self._refresh_lifecycle_locked()
                    self._condition.notify_all()
                    return
                operation.state = _ScopedReleaseState.RUNNING
                self._refresh_lifecycle_locked()

            result: ReleaseResult | None = None
            listeners: tuple[Callable[[ReleaseResult], None], ...] = ()
            with self._operation_lock:
                with self._state_lock:
                    if operation.state != _ScopedReleaseState.RUNNING:
                        continue
                    if self._pending_release or self._release_in_progress:
                        global_id = (
                            self._pending_global_operation_id
                            or self._current_global_operation_id
                            or "global_release"
                        )
                        self._supersede_one_scoped_locked(operation, global_id)
                        self._refresh_lifecycle_locked()
                        continue
                    if operation.target.runtime_epoch != self._runtime_epoch:
                        result = self._scoped_failure_result(
                            operation,
                            "runtime epoch changed before scoped release execution",
                        )
                    elif not self._owned or self._mode != operation.mode or not operation.owned:
                        result = self._scoped_failure_result(
                            operation,
                            "runtime ownership or mode changed before scoped release execution",
                        )
                    router_client = self._router_client

            # Do not retain the broad operation lock across router/process I/O:
            # exact admissions for unrelated router models must remain available.
            # The mutation lock still excludes global release and manager-owned
            # lifecycle changes.  Recheck after acquiring it because a global
            # operation may have won the handoff between the two locks.
            with self._runtime_mutation_lock:
                with self._state_lock:
                    if operation.state != _ScopedReleaseState.RUNNING:
                        continue
                    if self._pending_release or self._release_in_progress:
                        global_id = (
                            self._pending_global_operation_id
                            or self._current_global_operation_id
                            or "global_release"
                        )
                        self._supersede_one_scoped_locked(operation, global_id)
                        self._refresh_lifecycle_locked()
                        continue
                    if operation.target.runtime_epoch != self._runtime_epoch:
                        result = self._scoped_failure_result(
                            operation,
                            "runtime epoch changed before scoped release execution",
                        )
                    elif not self._owned or self._mode != operation.mode or not operation.owned:
                        result = self._scoped_failure_result(
                            operation,
                            "runtime ownership or mode changed before scoped release execution",
                        )
                    router_client = self._router_client

                if result is None:
                    result = self._execute_scoped_release(operation, router_client)

                with self._state_lock:
                    listeners = self._complete_scoped_operation_locked(
                        operation,
                        result,
                        signal=False,
                    )
                try:
                    self._notify_listeners(result, listeners)
                finally:
                    with self._state_lock:
                        self._signal_scoped_operation_locked(operation)

    def _execute_scoped_release(
        self,
        operation: _ScopedReleaseOperation,
        router_client: RouterReleaseClient | None,
    ) -> ReleaseResult:
        try:
            if operation.target.scope == ReleaseScope.DIRECT_RUNTIME:
                result = self._release_direct(
                    operation.operation_id,
                    self._scoped_source(operation),
                    RuntimeMode.DIRECT,
                    operation.started_at,
                )
            elif operation.target.scope == ReleaseScope.ROUTER_MODEL:
                result = self._release_router_model(
                    operation,
                    router_client,
                )
            else:  # pragma: no cover - ReleaseTarget rejects unsupported scoped targets
                raise RuntimeError(f"unsupported scoped release: {operation.target.scope.value}")
            return replace(
                result,
                scope=operation.target.scope,
                operation_id=operation.operation_id,
                target_model=operation.target.model_id,
                runtime_epoch=operation.target.runtime_epoch,
            )
        except BaseException as exc:
            return self._scoped_failure_result(
                operation,
                f"scoped release failed ({type(exc).__name__})",
            )

    def _release_router_model(
        self,
        operation: _ScopedReleaseOperation,
        client: RouterReleaseClient | None,
    ) -> ReleaseResult:
        model_id = operation.target.model_id
        if model_id is None:  # pragma: no cover - ReleaseTarget invariant
            return self._scoped_failure_result(operation, "exact router model ID is unavailable")
        if client is None:
            return self._scoped_failure_result(operation, "router release client is unavailable")

        models = tuple(self._normalize_router_model(item) for item in client.list_models())
        model_ids = tuple(model.model_id for model in models)
        if len(model_ids) != len(set(model_ids)):
            return self._scoped_failure_result(
                operation,
                "router returned duplicate exact model identities",
            )
        matches = tuple(model for model in models if model.model_id == model_id)
        if not matches:
            diagnostic = {"id": model_id, "state": "not_loaded", "raw": {}}
            return ReleaseResult(
                request_id=operation.operation_id,
                source=self._scoped_source(operation),
                status=ReleaseStatus.NOOP,
                mode=RuntimeMode.ROUTER,
                owned=True,
                started_at=operation.started_at,
                completed_at=time.time(),
                released_model_states=((model_id, "not_loaded"),),
                released_model_diagnostics=(diagnostic,),
            )

        current = matches[0]
        if current.state == "unknown":
            return self._scoped_failure_result(
                operation,
                f"router returned unknown residency for exact model {model_id!r}",
            )
        if current.state in _TERMINAL_NONRESIDENT_STATES:
            return ReleaseResult(
                request_id=operation.operation_id,
                source=self._scoped_source(operation),
                status=ReleaseStatus.NOOP,
                mode=RuntimeMode.ROUTER,
                owned=True,
                started_at=operation.started_at,
                completed_at=time.time(),
                released_model_states=((model_id, current.state),),
                released_model_diagnostics=(self._router_model_diagnostic(current),),
            )
        if current.state not in _RESIDENT_ROUTER_STATES:
            return self._scoped_failure_result(
                operation,
                "router model is in an unsupported nonterminal residency state",
            )

        terminal = client.unload_model(model_id, timeout=self._router_release_timeout)
        terminal = getattr(terminal, "model", terminal)
        normalized = self._normalize_router_model(terminal, fallback_id=model_id)
        if normalized.model_id != model_id:
            return self._scoped_failure_result(
                operation,
                "router barrier returned a mismatched exact model identity",
            )
        if normalized.state not in _TERMINAL_UNLOADED_STATES:
            return self._scoped_failure_result(
                operation,
                f"model {model_id!r} did not reach terminal unloaded state",
            )
        return ReleaseResult(
            request_id=operation.operation_id,
            source=self._scoped_source(operation),
            status=ReleaseStatus.COMPLETE,
            mode=RuntimeMode.ROUTER,
            owned=True,
            started_at=operation.started_at,
            completed_at=time.time(),
            released_models=(model_id,),
            released_model_states=((model_id, normalized.state),),
            released_model_diagnostics=(self._router_model_diagnostic(normalized),),
        )

    @staticmethod
    def _scoped_source(operation: _ScopedReleaseOperation) -> str:
        return "+".join(sorted(operation.sources)) or "generation"

    def _scoped_failure_result(
        self,
        operation: _ScopedReleaseOperation,
        error: str,
    ) -> ReleaseResult:
        return ReleaseResult(
            request_id=operation.operation_id,
            source=self._scoped_source(operation),
            status=ReleaseStatus.FAILED,
            mode=operation.mode,
            owned=operation.owned,
            started_at=operation.started_at,
            completed_at=time.time(),
            error=error,
            scope=operation.target.scope,
            operation_id=operation.operation_id,
            target_model=operation.target.model_id,
            runtime_epoch=operation.target.runtime_epoch,
        )

    def _fail_scoped_operation_locked(
        self,
        operation: _ScopedReleaseOperation,
        error: str,
    ) -> None:
        if operation.state == _ScopedReleaseState.TERMINAL:
            return
        result = self._scoped_failure_result(operation, error)
        self._complete_scoped_operation_locked(operation, result)

    def _complete_scoped_operation_locked(
        self,
        operation: _ScopedReleaseOperation,
        result: ReleaseResult,
        *,
        apply_runtime_transition: bool = True,
        signal: bool = True,
    ) -> tuple[Callable[[ReleaseResult], None], ...]:
        if operation.state in {
            _ScopedReleaseState.TRANSITIONING,
            _ScopedReleaseState.TERMINAL,
        }:
            return ()
        operation.state = _ScopedReleaseState.TRANSITIONING
        operation.result = result
        self._last_scoped_release = result

        direct_complete = (
            apply_runtime_transition
            and operation.target.scope == ReleaseScope.DIRECT_RUNTIME
            and operation.target.runtime_epoch == self._runtime_epoch
            and result.status in {ReleaseStatus.NOOP, ReleaseStatus.COMPLETE}
        )
        if direct_complete:
            self._mode = RuntimeMode.NONE
            self._owned = False
            self._router_client = None
            self._runtime_epoch += 1

        if result.success:
            self._last_error = None
        else:
            self._last_error = result.error
        self._refresh_lifecycle_locked()
        listeners = tuple(self._listeners)
        if signal:
            self._signal_scoped_operation_locked(operation)
        self._condition.notify_all()
        return listeners

    def _signal_scoped_operation_locked(self, operation: _ScopedReleaseOperation) -> None:
        if operation.result is None:  # pragma: no cover - defensive invariant
            raise RuntimeError("cannot signal scoped release without a terminal result")
        operation.state = _ScopedReleaseState.TERMINAL
        existing = self._scoped_operations.get(operation.key)
        if existing is operation:
            self._scoped_operations.pop(operation.key, None)
        self._refresh_lifecycle_locked()
        if not operation.result.success and self._active_generations == 0:
            self._lifecycle = RuntimeLifecycle.ERROR
        operation.event.set()
        self._condition.notify_all()

    def _supersede_one_scoped_locked(
        self,
        operation: _ScopedReleaseOperation,
        global_operation_id: str,
    ) -> None:
        if operation.state == _ScopedReleaseState.TERMINAL:
            return
        if operation.state == _ScopedReleaseState.DORMANT:
            operation.started_at = time.time()
        operation.state = _ScopedReleaseState.SUPERSEDED
        operation.superseded_by = global_operation_id

    def _supersede_scoped_operations_locked(self, global_operation_id: str) -> None:
        for operation in tuple(self._scoped_operations.values()):
            self._supersede_one_scoped_locked(operation, global_operation_id)

    def _complete_superseded_scoped_locked(
        self,
        global_result: ReleaseResult,
    ) -> tuple[_ScopedReleaseOperation, ...]:
        completed: list[_ScopedReleaseOperation] = []
        for operation in tuple(self._scoped_operations.values()):
            if operation.state != _ScopedReleaseState.SUPERSEDED:
                continue
            model_id = operation.target.model_id
            states = tuple(
                item for item in global_result.released_model_states if item[0] == model_id
            )
            diagnostics = tuple(
                item
                for item in global_result.released_model_diagnostics
                if item.get("id") == model_id
            )
            released = tuple(item for item in global_result.released_models if item == model_id)
            result = ReleaseResult(
                request_id=operation.operation_id,
                source=self._scoped_source(operation),
                status=global_result.status,
                mode=operation.mode,
                owned=operation.owned,
                started_at=operation.started_at,
                completed_at=global_result.completed_at,
                released_models=released,
                released_model_states=states,
                released_model_diagnostics=diagnostics,
                fallback_used=global_result.fallback_used,
                stop_result=global_result.stop_result,
                error=global_result.error,
                scope=operation.target.scope,
                operation_id=operation.operation_id,
                target_model=model_id,
                runtime_epoch=operation.target.runtime_epoch,
                superseded_by=global_result.operation_id or global_result.request_id,
            )
            self._complete_scoped_operation_locked(
                operation,
                result,
                apply_runtime_transition=False,
                signal=False,
            )
            completed.append(operation)
        return tuple(completed)

    def _refresh_lifecycle_locked(self) -> None:
        if self._release_in_progress:
            self._lifecycle = RuntimeLifecycle.RELEASING
        elif self._pending_release:
            self._lifecycle = RuntimeLifecycle.RELEASE_PENDING
        elif self._active_generations:
            self._lifecycle = RuntimeLifecycle.GENERATING
        elif any(
            operation.state
            in {
                _ScopedReleaseState.RUNNING,
                _ScopedReleaseState.TRANSITIONING,
            }
            for operation in self._scoped_operations.values()
        ):
            self._lifecycle = RuntimeLifecycle.RELEASING
        elif self._has_active_scoped_operations_locked():
            self._lifecycle = RuntimeLifecycle.RELEASE_PENDING
        elif self._last_error and self._lifecycle == RuntimeLifecycle.ERROR:
            return
        else:
            self._lifecycle = (
                RuntimeLifecycle.READY if self._mode != RuntimeMode.NONE else RuntimeLifecycle.IDLE
            )

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

        observed_in_progress = False
        with self._state_lock:
            observed_generation = self._release_generation
            observed_in_progress = self._release_in_progress
            if observed_in_progress:
                if not wait_for_coalesced:
                    return ReleaseResult(
                        request_id=request_id,
                        source=source,
                        status=ReleaseStatus.COALESCED,
                        mode=self._mode,
                        owned=self._owned,
                        started_at=started_at,
                        coalesced=True,
                        operation_id=self._current_global_operation_id,
                        runtime_epoch=self._runtime_epoch,
                    )

        # No lifecycle state is mutated before this request owns the shared
        # operation authority.  This makes /free wait behind startup/stop and
        # evaluate the state those operations actually leave behind.
        with self._operation_lock, self._runtime_mutation_lock:
            with self._state_lock:
                release_completed_while_waiting = (
                    self._release_generation > observed_generation
                    or (
                        observed_in_progress
                        and self._last_release_generation >= observed_generation
                    )
                )
                if (
                    release_completed_while_waiting
                    and not self._release_in_progress
                    and self._last_release is not None
                    and self._last_release_generation == self._release_generation
                ):
                    return replace(
                        self._last_release,
                        request_id=request_id,
                        source=source,
                        coalesced=True,
                    )

                mode = self._mode
                owned = self._owned
                if self._active_generations > 0:
                    self._pending_release = True
                    self._pending_sources.add(source)
                    if self._pending_global_operation_id is None:
                        self._pending_global_operation_id = request_id
                    self._supersede_scoped_operations_locked(self._pending_global_operation_id)
                    self._lifecycle = RuntimeLifecycle.RELEASE_PENDING
                    result = ReleaseResult(
                        request_id=request_id,
                        source=source,
                        status=ReleaseStatus.DEFERRED,
                        mode=mode,
                        owned=owned,
                        started_at=started_at,
                        operation_id=self._pending_global_operation_id,
                        runtime_epoch=self._runtime_epoch,
                    )
                    self._last_release = result
                    return result

                # A failed/incomplete startup can leave a positively owned
                # process before the service has been configured.  Native free
                # still has authority to clean up that exact process.
                orphaned_owned_process = (
                    mode == RuntimeMode.NONE and self._process.has_owned_process
                )
                if orphaned_owned_process:
                    mode = RuntimeMode.DIRECT
                    owned = True

                if not owned or mode in {RuntimeMode.NONE, RuntimeMode.ATTACHED}:
                    global_operation_id = self._pending_global_operation_id or request_id
                    pending_sources = set(self._pending_sources)
                    if not pending_sources or source != "deferred":
                        pending_sources.add(source)
                    release_source = "+".join(sorted(pending_sources))
                    self._pending_release = False
                    self._pending_sources.clear()
                    self._pending_global_operation_id = None
                    result = ReleaseResult(
                        request_id=request_id,
                        source=release_source,
                        status=ReleaseStatus.NOOP,
                        mode=mode,
                        owned=owned,
                        started_at=started_at,
                        completed_at=time.time(),
                        operation_id=global_operation_id,
                        runtime_epoch=self._runtime_epoch,
                    )
                    self._last_release = result
                    self._refresh_lifecycle_locked()
                    listeners = tuple(self._listeners)
                else:
                    global_operation_id = self._pending_global_operation_id or request_id
                    self._supersede_scoped_operations_locked(global_operation_id)
                    pending_sources = set(self._pending_sources)
                    if not pending_sources or source != "deferred":
                        pending_sources.add(source)
                    release_source = "+".join(sorted(pending_sources))
                    release_generation = self._begin_release_locked(global_operation_id)
                    router_client = self._router_client
                    release_epoch = self._runtime_epoch
                    result = None
                    listeners = ()

            if result is not None:
                self._notify_listeners(result, listeners)
                return result

            result = self._execute_release_locked(
                source=release_source,
                request_id=global_operation_id,
                started_at=started_at,
                mode=mode,
                owned=owned,
                router_client=router_client,
            )
            result = replace(
                result,
                operation_id=global_operation_id,
                runtime_epoch=release_epoch,
            )
            self._finish_release(result, release_generation)
            return replace(result, request_id=request_id)

    def _begin_release_locked(self, operation_id: str) -> int:
        self._release_in_progress = True
        self._release_generation += 1
        self._pending_release = False
        self._pending_sources.clear()
        self._pending_global_operation_id = None
        self._current_global_operation_id = operation_id
        self._lifecycle = RuntimeLifecycle.RELEASING
        return self._release_generation

    def _execute_release_locked(
        self,
        *,
        source: str,
        request_id: str,
        started_at: float,
        mode: RuntimeMode,
        owned: bool,
        router_client: RouterReleaseClient | None,
    ) -> ReleaseResult:
        try:
            if mode == RuntimeMode.DIRECT:
                return self._release_direct(request_id, source, mode, started_at)
            if mode == RuntimeMode.ROUTER:
                return self._release_router(
                    request_id,
                    source,
                    mode,
                    started_at,
                    router_client,
                )
            raise RuntimeError(f"unsupported runtime mode: {mode.value}")
        except Exception as exc:
            return ReleaseResult(
                request_id=request_id,
                source=source,
                status=ReleaseStatus.FAILED,
                mode=mode,
                owned=owned,
                started_at=started_at,
                completed_at=time.time(),
                error=f"runtime release failed ({type(exc).__name__})",
            )

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
                (),
                (),
                "router release client is unavailable",
            )

        released: list[str] = []
        released_states: list[tuple[str, str]] = []
        released_diagnostics: list[Mapping[str, Any]] = []
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
                released_states.append((model.model_id, normalized.state))
                released_diagnostics.append(self._router_model_diagnostic(normalized))
        except Exception as exc:
            return self._router_fallback(
                request_id,
                source,
                mode,
                started_at,
                tuple(released),
                tuple(released_states),
                tuple(released_diagnostics),
                f"router barrier failed ({type(exc).__name__})",
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
            released_model_states=tuple(released_states),
            released_model_diagnostics=tuple(released_diagnostics),
        )

    def _router_fallback(
        self,
        request_id: str,
        source: str,
        mode: RuntimeMode,
        started_at: float,
        released_models: tuple[str, ...],
        released_model_states: tuple[tuple[str, str], ...],
        released_model_diagnostics: tuple[Mapping[str, Any], ...],
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
                released_model_states=released_model_states,
                released_model_diagnostics=released_model_diagnostics,
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
            released_model_states=released_model_states,
            released_model_diagnostics=released_model_diagnostics,
            fallback_used=True,
            stop_result=stop_result,
            error=None if stop_result.complete else (stop_result.error or reason),
        )

    def _finish_release(self, result: ReleaseResult, release_generation: int) -> None:
        with self._state_lock:
            self._last_release = result
            self._last_release_generation = release_generation
            if result.success:
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
                    self._runtime_epoch += 1
            else:
                self._last_error = result.error
            superseded_operations = self._complete_superseded_scoped_locked(result)
            self._refresh_lifecycle_locked()
            listeners = tuple(self._listeners)
            self._condition.notify_all()

        try:
            self._notify_listeners(result, listeners)
        finally:
            with self._state_lock:
                self._release_in_progress = False
                self._current_global_operation_id = None
                self._refresh_lifecycle_locked()
                if not result.success:
                    self._lifecycle = RuntimeLifecycle.ERROR
                for operation in superseded_operations:
                    self._signal_scoped_operation_locked(operation)
                self._condition.notify_all()

    def _notify_listeners(
        self,
        result: ReleaseResult,
        listeners: Sequence[Callable[[ReleaseResult], None]],
    ) -> None:
        """Deliver release transitions without recursive callback growth.

        A listener may synchronously request another release.  Queue that
        transition behind the current listener snapshot instead of recursively
        entering the callback stack.  The generous hard cap makes even a
        permanently self-triggering listener fail finite and fail open.
        """

        queue = getattr(self._listener_delivery, "queue", None)
        if queue is None:
            queue = deque()
            self._listener_delivery.queue = queue
        queue.append((result, tuple(listeners)))
        if getattr(self._listener_delivery, "draining", False):
            return

        self._listener_delivery.draining = True
        delivered = 0
        try:
            while queue and delivered < _MAX_REENTRANT_LISTENER_NOTIFICATIONS:
                queued_result, queued_listeners = queue.popleft()
                delivered += 1
                for listener in queued_listeners:
                    try:
                        listener(queued_result)
                    except BaseException as exc:
                        LOGGER.warning(
                            "runtime release listener failed (%s)",
                            type(exc).__name__,
                        )
            if queue:
                dropped = len(queue)
                queue.clear()
                LOGGER.warning(
                    "runtime release listener notification limit reached; dropped %d transition(s)",
                    dropped,
                )
        finally:
            queue.clear()
            self._listener_delivery.draining = False

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

    @staticmethod
    def _router_model_diagnostic(model: RouterReleaseModel) -> Mapping[str, Any]:
        # ``raw`` is retained as an empty compatibility key.  Upstream model
        # records are untrusted and can contain server implementation details,
        # prompt fragments, credentials, or arbitrary nested response fields.
        return {"id": model.model_id, "state": model.state, "raw": {}}

    def diagnostics(self) -> dict[str, Any]:
        # Diagnostics is observability, not a lifecycle mutation.  Waiting on the
        # operation lock would make status unavailable throughout model startup,
        # shutdown, and router barriers.  Copy each owner's state under its own
        # short lock instead.  During a transition the two snapshots can describe
        # adjacent instants, which is truthful and preferable to blocking status.
        with self._state_lock:
            last_release = self._last_release
            last_scoped_release = self._last_scoped_release
            scoped_releases = [
                {
                    "operation_id": operation.operation_id,
                    "scope": operation.target.scope.value,
                    "target_model": operation.target.model_id,
                    "runtime_epoch": operation.target.runtime_epoch,
                    "state": operation.state.value,
                    "superseded_by": operation.superseded_by,
                }
                for operation in self._scoped_operations.values()
            ]
            data = {
                "mode": self._mode.value,
                "owned": self._owned,
                "runtime_epoch": self._runtime_epoch,
                "lifecycle": self._lifecycle.value,
                "active_generations": self._active_generations,
                "active_direct_generations": self._active_direct_generations,
                "active_router_generations": dict(self._active_router_generations),
                "active_unknown_router_generations": (self._active_unknown_router_generations),
                "release_pending": self._pending_release,
                "release_in_progress": self._release_in_progress,
                "scoped_release_pending": bool(scoped_releases),
                "scoped_releases": scoped_releases,
                "last_error": self._last_error,
                "last_release": last_release.as_dict() if last_release else None,
                "last_scoped_release": (
                    last_scoped_release.as_dict() if last_scoped_release else None
                ),
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
    "GenerationLeaseToken",
    "ReleaseHandle",
    "ReleaseResult",
    "ReleaseScope",
    "ReleaseStatus",
    "ReleaseTarget",
    "ReleaseWaitCancelled",
    "ReleaseWaitTimeout",
    "RouterReleaseModel",
    "RouterReleaseClient",
    "RuntimeLifecycle",
    "RuntimeMode",
    "RuntimeOperationBusy",
    "RuntimeReleasePending",
    "RuntimeService",
    "get_runtime_service",
]
