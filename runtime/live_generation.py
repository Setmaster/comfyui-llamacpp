"""Bounded, Comfy-independent state for live generation observability.

The registry owns no HTTP routes and imports no ComfyUI modules.  Integrations
provide a targeted sender and, for positively capability-probed streams, one
exact cancellation callback.  Workflow results remain separate from this
ephemeral UI state.
"""

from __future__ import annotations

import json
import math
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .client import StreamCleanupOutcome, StreamCleanupSnapshot, redact_secrets
from .streaming import PromptProgress, StreamResult, StreamUpdate

LIVE_EVENT = "llamacpp.generation"
LIVE_SCHEMA_VERSION = 1
MAX_PREVIEW_BYTES = 64 * 1024
MAX_EVENT_BYTES = 160 * 1024
MAX_ACTIVE_EXECUTIONS = 32
MAX_RESTORE_EXECUTIONS = 8
EMIT_INTERVAL_SECONDS = 0.125

_MAX_IDENTIFIER_BYTES = 512
_MAX_ERROR_BYTES = 2048
_MAX_FINISH_REASON_BYTES = 128
_MAX_MODEL_BYTES = 512
_MAX_SAFE_INTEGER = (1 << 53) - 1
_PREVIEW_CHUNK_CHARACTERS = 4096
_TERMINAL_PHASES = frozenset({"complete", "cancelled", "failed"})
_PHASES = frozenset(
    {
        "starting",
        "loading",
        "reasoning",
        "generating",
        "cancelling",
        "releasing",
        *_TERMINAL_PHASES,
    }
)
_UNSET = object()

EventSender = Callable[[str, dict[str, Any], str], Any]
CancelCallback = Callable[[], bool]
Clock = Callable[[], float]
TimerFactory = Callable[[float, Callable[[], None]], Any]


class LiveGenerationCapacityError(RuntimeError):
    pass


class CancelScope(str, Enum):
    NONE = "none"
    GENERATION = "generation"
    PROMPT = "prompt"


@dataclass(frozen=True, slots=True)
class ExecutionIdentity:
    execution_id: uuid.UUID
    prompt_id: str | None
    node_id: str
    display_node_id: str
    real_node_id: str
    parent_node_id: str | None = None
    list_index: int | None = None
    workflow_id: str | None = None
    client_id: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.execution_id, uuid.UUID):
            raise TypeError("execution_id must be a UUID")
        if self.execution_id.version != 4:
            raise ValueError("execution_id must be an internally generated UUID4")
        for name in ("node_id", "display_node_id", "real_node_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
            if (
                len(value) > _MAX_IDENTIFIER_BYTES
                or len(value.encode("utf-8")) > _MAX_IDENTIFIER_BYTES
            ):
                raise ValueError(f"{name} exceeds the identifier limit")
        for name in ("prompt_id", "parent_node_id", "workflow_id", "client_id"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, str)
                or not value
                or len(value) > _MAX_IDENTIFIER_BYTES
                or len(value.encode("utf-8")) > _MAX_IDENTIFIER_BYTES
            ):
                raise ValueError(f"{name} must be a bounded non-empty string or None")
        if self.list_index is not None and (
            isinstance(self.list_index, bool)
            or not isinstance(self.list_index, int)
            or self.list_index < 0
            or self.list_index > _MAX_SAFE_INTEGER
        ):
            raise ValueError("list_index must be a non-negative integer or None")

    @classmethod
    def create(
        cls,
        *,
        prompt_id: str | None,
        node_id: object,
        display_node_id: object | None = None,
        real_node_id: object | None = None,
        parent_node_id: object | None = None,
        list_index: int | None = None,
        workflow_id: str | None = None,
        client_id: str | None = None,
    ) -> ExecutionIdentity:
        if node_id is None:
            raise ValueError("node_id must not be None")
        node = str(node_id)
        return cls(
            execution_id=uuid.uuid4(),
            prompt_id=prompt_id,
            node_id=node,
            display_node_id=node if display_node_id is None else str(display_node_id),
            real_node_id=node if real_node_id is None else str(real_node_id),
            parent_node_id=None if parent_node_id is None else str(parent_node_id),
            list_index=list_index,
            workflow_id=workflow_id or None,
            client_id=client_id or None,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "execution_id": str(self.execution_id),
            "prompt_id": self.prompt_id,
            "node_id": self.node_id,
            "display_node_id": self.display_node_id,
            "real_node_id": self.real_node_id,
            "parent_node_id": self.parent_node_id,
            "list_index": self.list_index,
            "workflow_id": self.workflow_id,
        }


class CancellationToken:
    """Thread-safe, monotonic local cancellation signal."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> bool:
        first = not self._event.is_set()
        self._event.set()
        return first

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def __call__(self) -> bool:
        return self.cancelled

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)


def _utf8_head(value: object, limit: int) -> str:
    if value is None:
        return ""
    text = str(value)
    raw = text[:limit].encode("utf-8")
    if len(raw) <= limit:
        return raw.decode("utf-8")
    return raw[:limit].decode("utf-8", errors="ignore")


def _utf8_tail(value: str, limit: int) -> tuple[str, int]:
    if limit <= 0:
        return "", 0
    raw = value[-limit:].encode("utf-8")
    if len(raw) <= limit:
        return value[-limit:], len(raw)
    text = raw[-limit:].decode("utf-8", errors="ignore")
    return text, len(text.encode("utf-8"))


class _PreviewTail:
    def __init__(self, limit: int = MAX_PREVIEW_BYTES) -> None:
        self.limit = limit
        self._value = ""
        self.total_bytes = 0

    def append(self, value: str) -> None:
        if not isinstance(value, str):
            raise TypeError("preview updates must be strings")
        tail = self._value.encode("utf-8")
        for offset in range(0, len(value), _PREVIEW_CHUNK_CHARACTERS):
            encoded = value[offset : offset + _PREVIEW_CHUNK_CHARACTERS].encode("utf-8")
            self.total_bytes += len(encoded)
            tail = (tail + encoded)[-(self.limit + 3) :]
        self._value = tail[-self.limit :].decode("utf-8", errors="ignore")

    def replace(self, value: str) -> None:
        if not isinstance(value, str):
            raise TypeError("preview values must be strings")
        self._value = ""
        self.total_bytes = 0
        self.append(value)

    def snapshot(self) -> PreviewPane:
        stored_bytes = len(self._value.encode("utf-8"))
        return PreviewPane(
            self._value,
            stored_bytes,
            self.total_bytes,
            self.total_bytes > stored_bytes,
        )


@dataclass(frozen=True, slots=True)
class PreviewPane:
    text: str
    bytes: int
    total_bytes: int
    truncated: bool

    def as_dict(self, limit: int = MAX_PREVIEW_BYTES) -> dict[str, Any]:
        text, stored_bytes = _utf8_tail(self.text, limit)
        return {
            "text": text,
            "bytes": stored_bytes,
            "total_bytes": self.total_bytes,
            "truncated": self.truncated or stored_bytes < self.bytes,
        }


def _normalized_usage(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    result: dict[str, Any] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        candidate = value.get(key)
        if (
            isinstance(candidate, int)
            and not isinstance(candidate, bool)
            and 0 <= candidate <= _MAX_SAFE_INTEGER
        ):
            result[key] = candidate
    details = value.get("prompt_tokens_details")
    if isinstance(details, Mapping):
        cached = details.get("cached_tokens")
        if (
            isinstance(cached, int)
            and not isinstance(cached, bool)
            and 0 <= cached <= _MAX_SAFE_INTEGER
        ):
            result["prompt_tokens_details"] = {"cached_tokens": cached}
    return result or None


def _normalized_model_progress(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    state = _utf8_head(value.get("state"), 64) or "unknown"
    percent = value.get("percent")
    if (
        not isinstance(percent, (int, float))
        or isinstance(percent, bool)
        or not math.isfinite(percent)
    ):
        percent = None
    elif percent < 0 or percent > 100:
        percent = None
    return {"state": state, "percent": percent}


def _normalized_release(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    status = _utf8_head(value.get("status"), 64)
    if not status:
        return None
    result: dict[str, Any] = {
        "status": status,
        "terminal": bool(value.get("terminal", False)),
    }
    for name, limit in (
        ("policy", 64),
        ("mode", 64),
        ("scope", 64),
        ("target_model", _MAX_MODEL_BYTES),
        ("request_id", 128),
        ("operation_id", 128),
    ):
        candidate = _utf8_head(value.get(name), limit)
        if candidate:
            result[name] = candidate
    if type(value.get("coalesced")) is bool:
        result["coalesced"] = value["coalesced"]
    if type(value.get("success")) is bool:
        result["success"] = value["success"]
    return result


def _normalized_error(value: object) -> dict[str, str] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        category = _utf8_head(value.get("category", value.get("type", "generation")), 64)
        message = _utf8_head(value.get("message"), _MAX_ERROR_BYTES * 2)
    else:
        category = "generation"
        message = _utf8_head(value, _MAX_ERROR_BYTES * 2)
    message = _utf8_head(redact_secrets(message), _MAX_ERROR_BYTES)
    if not message:
        return None
    return {"category": category or "generation", "message": message}


def _normalized_stream_cleanup(value: object) -> dict[str, Any] | None:
    if isinstance(value, (StreamCleanupOutcome, StreamCleanupSnapshot)):
        value = value.as_dict()
    if not isinstance(value, Mapping):
        return None

    def count(name: str) -> int:
        candidate = value.get(name)
        if (
            isinstance(candidate, int)
            and not isinstance(candidate, bool)
            and 0 <= candidate <= _MAX_SAFE_INTEGER
        ):
            return candidate
        return 0

    error_type = _utf8_head(value.get("last_error_type"), 128) or None
    return {
        "attempts": count("attempts"),
        "confirmed": value.get("confirmed") is True,
        "failures": count("failures"),
        "last_error_type": error_type,
    }


@dataclass(frozen=True, slots=True)
class LiveGenerationSnapshot:
    identity: ExecutionIdentity
    sequence: int
    phase: str
    terminal: bool
    started_at_ms: int
    elapsed_ms: int
    model: str | None
    response: PreviewPane
    thinking: PreviewPane
    prompt_progress: PromptProgress | None
    model_progress: Mapping[str, Any] | None
    finish_reason: str | None
    usage: Mapping[str, Any] | None
    partial: bool
    cancelled: bool
    cancel_scope: CancelScope
    cancel_enabled: bool
    release: Mapping[str, Any] | None
    stream_cleanup: Mapping[str, Any] | None
    error: Mapping[str, str] | None
    chunks: int

    def _payload(self, preview_limit: int) -> dict[str, Any]:
        scope = self.cancel_scope.value
        label = {
            CancelScope.GENERATION: "Stop generation",
            CancelScope.PROMPT: "Stop Comfy job",
            CancelScope.NONE: None,
        }[self.cancel_scope]
        return {
            "schema_version": LIVE_SCHEMA_VERSION,
            "kind": "snapshot",
            **self.identity.as_dict(),
            "sequence": self.sequence,
            "phase": self.phase,
            "terminal": self.terminal,
            "started_at_ms": self.started_at_ms,
            "elapsed_ms": self.elapsed_ms,
            "model": self.model,
            "response": self.response.as_dict(preview_limit),
            "thinking": self.thinking.as_dict(preview_limit),
            "prompt_progress": (
                self.prompt_progress.as_dict() if self.prompt_progress is not None else None
            ),
            "model_progress": dict(self.model_progress) if self.model_progress else None,
            "finish_reason": self.finish_reason,
            "usage": dict(self.usage) if self.usage else None,
            "partial": self.partial,
            "cancelled": self.cancelled,
            "cancel": {
                "enabled": self.cancel_enabled,
                "scope": scope,
                "label": label,
            },
            "release": dict(self.release) if self.release else None,
            "stream_cleanup": dict(self.stream_cleanup) if self.stream_cleanup else None,
            "error": dict(self.error) if self.error else None,
            "chunks": self.chunks,
        }

    def as_dict(self) -> dict[str, Any]:
        preview_limit = MAX_PREVIEW_BYTES
        while True:
            payload = self._payload(preview_limit)
            encoded = json.dumps(
                {"type": LIVE_EVENT, "data": payload},
                ensure_ascii=True,
            ).encode("utf-8")
            if len(encoded) < MAX_EVENT_BYTES:
                return payload
            if preview_limit == 0:
                # All non-preview fields are normalized and individually bounded,
                # so reaching this branch would be a programming error.
                raise ValueError("normalized live generation payload exceeds its byte limit")
            preview_limit = preview_limit // 2 if preview_limit > 1024 else 0


@dataclass(frozen=True, slots=True)
class CancelResult:
    accepted: bool
    scope: CancelScope
    status: str
    upstream_confirmed: bool = False
    error_type: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "scope": self.scope.value,
            "status": self.status,
            "upstream_confirmed": self.upstream_confirmed,
            "error_type": self.error_type,
        }


def _default_timer(delay: float, callback: Callable[[], None]) -> threading.Timer:
    timer = threading.Timer(delay, callback)
    timer.daemon = True
    timer.start()
    return timer


@dataclass(slots=True)
class _LiveExecution:
    identity: ExecutionIdentity
    token: CancellationToken
    cancel_scope: CancelScope
    cancel_callback: CancelCallback | None
    started_monotonic: float
    started_at_ms: int
    phase: str = "starting"
    terminal: bool = False
    sequence: int = 1
    model: str | None = None
    response: _PreviewTail = field(default_factory=_PreviewTail)
    thinking: _PreviewTail = field(default_factory=_PreviewTail)
    prompt_progress: PromptProgress | None = None
    model_progress: Mapping[str, Any] | None = None
    finish_reason: str | None = None
    usage: Mapping[str, Any] | None = None
    partial: bool = False
    cancelled: bool = False
    release: Mapping[str, Any] | None = None
    stream_cleanup: Mapping[str, Any] | None = None
    error: Mapping[str, str] | None = None
    chunks: int = 0
    last_emit: float = -math.inf
    pending_timer: Any = None
    pending_timer_token: object | None = None
    cancel_requested: bool = False


class LiveGenerationHandle:
    def __init__(
        self,
        registry: LiveGenerationRegistry,
        identity: ExecutionIdentity,
        token: CancellationToken,
    ) -> None:
        self._registry = registry
        self.identity = identity
        self.token = token

    @property
    def active(self) -> bool:
        return self._registry.snapshot(self.identity.execution_id) is not None

    def set_cancel(
        self,
        scope: CancelScope,
        callback: CancelCallback | None = None,
    ) -> LiveGenerationSnapshot | None:
        return self._registry.set_cancel(self.identity.execution_id, scope, callback)

    def set_phase(self, phase: str, **values: Any) -> LiveGenerationSnapshot | None:
        return self._registry.update(self.identity.execution_id, phase=phase, **values)

    def update(self, **values: Any) -> LiveGenerationSnapshot | None:
        return self._registry.update(self.identity.execution_id, **values)

    def on_stream_update(self, update: StreamUpdate) -> LiveGenerationSnapshot | None:
        return self._registry.apply_stream_update(self.identity.execution_id, update)

    def record_result(self, result: StreamResult) -> LiveGenerationSnapshot | None:
        return self._registry.record_result(self.identity.execution_id, result)

    def finish(
        self,
        *,
        phase: str,
        result: StreamResult | None = None,
        release: Mapping[str, Any] | None = None,
        stream_cleanup: object = _UNSET,
        error: object = None,
    ) -> LiveGenerationSnapshot | None:
        return self._registry.finish(
            self.identity.execution_id,
            phase=phase,
            result=result,
            release=release,
            stream_cleanup=stream_cleanup,
            error=error,
        )


class LiveGenerationRegistry:
    """Thread-safe bounded registry for active generation UI state."""

    def __init__(
        self,
        *,
        sender: EventSender | None = None,
        clock: Clock = time.monotonic,
        wall_clock: Clock = time.time,
        timer_factory: TimerFactory = _default_timer,
        max_active: int = MAX_ACTIVE_EXECUTIONS,
    ) -> None:
        if isinstance(max_active, bool) or not isinstance(max_active, int) or max_active <= 0:
            raise ValueError("max_active must be positive")
        self._sender = sender
        self._clock = clock
        self._wall_clock = wall_clock
        self._timer_factory = timer_factory
        self._max_active = max_active
        self._lock = threading.RLock()
        self._active: dict[uuid.UUID, _LiveExecution] = {}
        self._last_started_at_ms = -1

    def set_sender(self, sender: EventSender | None) -> None:
        with self._lock:
            self._sender = sender

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._active)

    def begin(
        self,
        identity: ExecutionIdentity,
        *,
        cancel_scope: CancelScope = CancelScope.PROMPT,
        cancel_callback: CancelCallback | None = None,
    ) -> LiveGenerationHandle:
        if not isinstance(identity, ExecutionIdentity):
            raise TypeError("identity must be an ExecutionIdentity")
        if not isinstance(cancel_scope, CancelScope):
            cancel_scope = CancelScope(cancel_scope)
        if cancel_scope == CancelScope.GENERATION and cancel_callback is None:
            raise ValueError("generation-scoped cancellation requires an exact callback")
        if cancel_scope != CancelScope.GENERATION and cancel_callback is not None:
            raise ValueError("only generation-scoped cancellation accepts a callback")

        token = CancellationToken()
        now = self._clock()
        state = _LiveExecution(
            identity=identity,
            token=token,
            cancel_scope=cancel_scope,
            cancel_callback=cancel_callback,
            started_monotonic=now,
            started_at_ms=int(self._wall_clock() * 1000),
            last_emit=now,
        )
        with self._lock:
            if identity.execution_id in self._active:
                raise ValueError("execution_id is already active")
            if len(self._active) >= self._max_active:
                raise LiveGenerationCapacityError("live generation registry is full")
            state.started_at_ms = max(state.started_at_ms, self._last_started_at_ms + 1)
            self._last_started_at_ms = state.started_at_ms
            self._active[identity.execution_id] = state
            snapshot = self._snapshot_locked(state, now)
            delivery = self._delivery_locked(snapshot)
        self._deliver(delivery)
        return LiveGenerationHandle(self, identity, token)

    def _snapshot_locked(
        self,
        state: _LiveExecution,
        now: float | None = None,
    ) -> LiveGenerationSnapshot:
        current = self._clock() if now is None else now
        return LiveGenerationSnapshot(
            identity=state.identity,
            sequence=state.sequence,
            phase=state.phase,
            terminal=state.terminal,
            started_at_ms=state.started_at_ms,
            elapsed_ms=max(0, int((current - state.started_monotonic) * 1000)),
            model=state.model,
            response=state.response.snapshot(),
            thinking=state.thinking.snapshot(),
            prompt_progress=state.prompt_progress,
            model_progress=state.model_progress,
            finish_reason=state.finish_reason,
            usage=state.usage,
            partial=state.partial,
            cancelled=state.cancelled,
            cancel_scope=state.cancel_scope,
            cancel_enabled=(
                not state.terminal
                and not state.cancel_requested
                and state.cancel_scope != CancelScope.NONE
            ),
            release=state.release,
            stream_cleanup=state.stream_cleanup,
            error=state.error,
            chunks=state.chunks,
        )

    def snapshot(self, execution_id: uuid.UUID | str) -> LiveGenerationSnapshot | None:
        key = self._uuid(execution_id)
        with self._lock:
            state = self._active.get(key)
            return self._snapshot_locked(state) if state is not None else None

    def active_for_client(
        self,
        client_id: str,
        *,
        limit: int = MAX_RESTORE_EXECUTIONS,
    ) -> list[dict[str, Any]]:
        if (
            not isinstance(client_id, str)
            or not client_id
            or len(client_id) > _MAX_IDENTIFIER_BYTES
            or len(client_id.encode("utf-8")) > _MAX_IDENTIFIER_BYTES
        ):
            return []
        if isinstance(limit, bool) or not isinstance(limit, int):
            return []
        limit = max(0, min(limit, MAX_RESTORE_EXECUTIONS))
        if limit == 0:
            return []
        with self._lock:
            states = sorted(
                (state for state in self._active.values() if state.identity.client_id == client_id),
                key=lambda state: state.started_monotonic,
                reverse=True,
            )[:limit]
            snapshots = [self._snapshot_locked(state) for state in states]
        return [snapshot.as_dict() for snapshot in snapshots]

    def set_cancel(
        self,
        execution_id: uuid.UUID | str,
        scope: CancelScope,
        callback: CancelCallback | None = None,
    ) -> LiveGenerationSnapshot | None:
        if not isinstance(scope, CancelScope):
            scope = CancelScope(scope)
        if scope == CancelScope.GENERATION and callback is None:
            raise ValueError("generation-scoped cancellation requires an exact callback")
        if scope != CancelScope.GENERATION and callback is not None:
            raise ValueError("only generation-scoped cancellation accepts a callback")
        key = self._uuid(execution_id)
        return self.update(
            key,
            cancel_scope=scope,
            cancel_callback=callback,
        )

    def apply_stream_update(
        self,
        execution_id: uuid.UUID | str,
        update: StreamUpdate,
    ) -> LiveGenerationSnapshot | None:
        phase: str | None = None
        if update.content:
            phase = "generating"
        elif update.thinking:
            phase = "reasoning"
        elif update.prompt_progress is not None:
            phase = "loading"
        return self.update(
            execution_id,
            phase=phase,
            response_delta=update.content,
            thinking_delta=update.thinking,
            prompt_progress=(
                update.prompt_progress if update.prompt_progress is not None else _UNSET
            ),
            model=update.model if update.model is not None else _UNSET,
            finish_reason=(update.finish_reason if update.finish_reason is not None else _UNSET),
            usage=update.usage if update.usage is not None else _UNSET,
            chunks=update.chunk_index,
        )

    def record_result(
        self,
        execution_id: uuid.UUID | str,
        result: StreamResult,
    ) -> LiveGenerationSnapshot | None:
        return self.update(
            execution_id,
            response=result.response,
            thinking=result.thinking,
            prompt_progress=result.prompt_progress,
            model=result.model,
            finish_reason=result.finish_reason,
            usage=result.usage,
            partial=result.partial,
            cancelled=result.cancelled,
            chunks=result.chunks,
            stream_cleanup=result.stream_cleanup,
            error=(
                {
                    "category": result.error_type or "generation",
                    "message": result.error_message,
                }
                if result.error_message
                else None
            ),
        )

    def update(
        self,
        execution_id: uuid.UUID | str,
        *,
        phase: str | None = None,
        response_delta: str = "",
        thinking_delta: str = "",
        response: object = _UNSET,
        thinking: object = _UNSET,
        model: object = _UNSET,
        prompt_progress: object = _UNSET,
        model_progress: object = _UNSET,
        finish_reason: object = _UNSET,
        usage: object = _UNSET,
        partial: object = _UNSET,
        cancelled: object = _UNSET,
        release: object = _UNSET,
        stream_cleanup: object = _UNSET,
        error: object = _UNSET,
        chunks: object = _UNSET,
        cancel_scope: object = _UNSET,
        cancel_callback: object = _UNSET,
        force_emit: bool = False,
    ) -> LiveGenerationSnapshot | None:
        key = self._uuid(execution_id)
        with self._lock:
            state = self._active.get(key)
            if state is None or state.terminal:
                return None
            if phase is not None:
                self._validate_phase(phase, terminal=False)
                if not (
                    state.cancel_requested
                    and phase in {"starting", "loading", "reasoning", "generating"}
                ):
                    state.phase = phase
            if response_delta:
                state.response.append(response_delta)
            if thinking_delta:
                state.thinking.append(thinking_delta)
            if response is not _UNSET:
                state.response.replace(str(response or ""))
            if thinking is not _UNSET:
                state.thinking.replace(str(thinking or ""))
            if model is not _UNSET:
                text = _utf8_head(model, _MAX_MODEL_BYTES)
                state.model = text or None
            if prompt_progress is not _UNSET:
                state.prompt_progress = (
                    prompt_progress if isinstance(prompt_progress, PromptProgress) else None
                )
            if model_progress is not _UNSET:
                state.model_progress = _normalized_model_progress(model_progress)
            if finish_reason is not _UNSET:
                text = _utf8_head(finish_reason, _MAX_FINISH_REASON_BYTES)
                state.finish_reason = text or None
            if usage is not _UNSET:
                state.usage = _normalized_usage(usage)
            if partial is not _UNSET:
                state.partial = bool(partial)
            if cancelled is not _UNSET:
                state.cancelled = bool(cancelled)
            if release is not _UNSET:
                state.release = _normalized_release(release)
            if stream_cleanup is not _UNSET:
                state.stream_cleanup = _normalized_stream_cleanup(stream_cleanup)
            if error is not _UNSET:
                state.error = _normalized_error(error)
            if chunks is not _UNSET:
                if isinstance(chunks, int) and not isinstance(chunks, bool):
                    state.chunks = max(0, min(chunks, _MAX_SAFE_INTEGER))
            if cancel_scope is not _UNSET:
                scope = (
                    cancel_scope
                    if isinstance(cancel_scope, CancelScope)
                    else CancelScope(cancel_scope)
                )
                callback = state.cancel_callback if cancel_callback is _UNSET else cancel_callback
                if scope == CancelScope.GENERATION and callback is None:
                    raise ValueError("generation-scoped cancellation requires an exact callback")
                if scope != CancelScope.GENERATION and callback is not None:
                    raise ValueError("only generation-scoped cancellation accepts a callback")
                state.cancel_scope = scope
                state.cancel_callback = callback
            elif cancel_callback is not _UNSET:
                raise ValueError("cancel_callback cannot change without cancel_scope")

            if state.phase == "releasing":
                state.cancel_scope = CancelScope.NONE
                state.cancel_callback = None

            state.sequence += 1
            now = self._clock()
            snapshot = self._snapshot_locked(state, now)
            delivery = self._emit_or_schedule_locked(state, snapshot, now, force_emit)
        self._deliver(delivery)
        return snapshot

    def finish(
        self,
        execution_id: uuid.UUID | str,
        *,
        phase: str,
        result: StreamResult | None = None,
        release: Mapping[str, Any] | None = None,
        stream_cleanup: object = _UNSET,
        error: object = None,
    ) -> LiveGenerationSnapshot | None:
        self._validate_phase(phase, terminal=True)
        key = self._uuid(execution_id)
        with self._lock:
            state = self._active.get(key)
            if state is None or state.terminal:
                return None
            if result is not None:
                state.response.replace(result.response)
                state.thinking.replace(result.thinking)
                state.prompt_progress = result.prompt_progress
                state.model = _utf8_head(result.model, _MAX_MODEL_BYTES) or None
                state.finish_reason = (
                    _utf8_head(result.finish_reason, _MAX_FINISH_REASON_BYTES) or None
                )
                state.usage = _normalized_usage(result.usage)
                state.partial = result.partial
                state.cancelled = result.cancelled
                state.chunks = (
                    max(0, min(result.chunks, _MAX_SAFE_INTEGER))
                    if isinstance(result.chunks, int) and not isinstance(result.chunks, bool)
                    else 0
                )
                state.stream_cleanup = _normalized_stream_cleanup(result.stream_cleanup)
                if error is None and result.error_message:
                    error = {
                        "category": result.error_type or "generation",
                        "message": result.error_message,
                    }
            if stream_cleanup is not _UNSET:
                state.stream_cleanup = _normalized_stream_cleanup(stream_cleanup)
            state.phase = phase
            state.terminal = True
            state.cancelled = state.cancelled or phase == "cancelled"
            state.cancel_scope = CancelScope.NONE
            state.cancel_callback = None
            state.release = _normalized_release(release)
            state.error = _normalized_error(error)
            state.sequence += 1
            self._cancel_timer_locked(state)
            now = self._clock()
            state.last_emit = now
            snapshot = self._snapshot_locked(state, now)
            delivery = self._delivery_locked(snapshot)
            self._active.pop(key, None)
        self._deliver(delivery)
        return snapshot

    def cancel(
        self,
        execution_id: uuid.UUID | str,
        *,
        prompt_id: str | None = None,
        node_id: str | None = None,
    ) -> CancelResult:
        return self._cancel(
            execution_id,
            prompt_id=prompt_id,
            node_id=node_id,
            expected_client_id=_UNSET,
        )

    def cancel_for_client(
        self,
        execution_id: uuid.UUID | str,
        client_id: str,
        *,
        prompt_id: str | None = None,
        node_id: str | None = None,
    ) -> CancelResult:
        """Cancel only when the exact execution belongs to the requesting client."""

        if (
            not isinstance(client_id, str)
            or not client_id
            or len(client_id) > _MAX_IDENTIFIER_BYTES
            or len(client_id.encode("utf-8")) > _MAX_IDENTIFIER_BYTES
        ):
            return CancelResult(False, CancelScope.NONE, "not_active")
        return self._cancel(
            execution_id,
            prompt_id=prompt_id,
            node_id=node_id,
            expected_client_id=client_id,
        )

    def _cancel(
        self,
        execution_id: uuid.UUID | str,
        *,
        prompt_id: str | None,
        node_id: str | None,
        expected_client_id: object,
    ) -> CancelResult:
        key = self._uuid(execution_id)
        delivery = None
        with self._lock:
            state = self._active.get(key)
            if (
                state is None
                or state.terminal
                or (
                    expected_client_id is not _UNSET
                    and state.identity.client_id != expected_client_id
                )
                or (prompt_id is not None and state.identity.prompt_id != prompt_id)
                or (node_id is not None and state.identity.node_id != str(node_id))
            ):
                return CancelResult(False, CancelScope.NONE, "not_active")
            if state.cancel_scope != CancelScope.GENERATION or state.cancel_callback is None:
                return CancelResult(False, state.cancel_scope, "whole_job_required")
            callback = state.cancel_callback
            if state.cancel_requested:
                return CancelResult(True, CancelScope.GENERATION, "already_requested")
            state.cancel_requested = True
            state.token.cancel()
            state.phase = "cancelling"
            state.sequence += 1
            now = self._clock()
            snapshot = self._snapshot_locked(state, now)
            delivery = self._emit_or_schedule_locked(state, snapshot, now, False)
        self._deliver(delivery)

        try:
            confirmed = bool(callback())
        except Exception as exc:
            return CancelResult(
                True,
                CancelScope.GENERATION,
                "requested",
                upstream_confirmed=False,
                error_type=_utf8_head(type(exc).__name__, 128),
            )
        return CancelResult(
            True,
            CancelScope.GENERATION,
            "requested",
            upstream_confirmed=confirmed,
        )

    def _emit_or_schedule_locked(
        self,
        state: _LiveExecution,
        snapshot: LiveGenerationSnapshot,
        now: float,
        force: bool,
    ) -> tuple[EventSender, dict[str, Any], str] | None:
        if self._sender is None or state.identity.client_id is None:
            return None
        elapsed = now - state.last_emit
        if force or elapsed >= EMIT_INTERVAL_SECONDS:
            self._cancel_timer_locked(state)
            state.last_emit = now
            return self._delivery_locked(snapshot)
        if state.pending_timer is None:
            delay = max(0.0, EMIT_INTERVAL_SECONDS - elapsed)
            timer_token = object()
            state.pending_timer_token = timer_token
            try:
                timer = self._timer_factory(
                    delay,
                    lambda execution_id=state.identity.execution_id, token=timer_token: self._flush(
                        execution_id,
                        token,
                    ),
                )
            except Exception:
                # Live delivery is additive. A timer allocation failure must not
                # turn a valid generation into a workflow failure.
                if state.pending_timer_token is timer_token:
                    state.pending_timer_token = None
                return None
            if state.pending_timer_token is timer_token:
                state.pending_timer = timer
            else:
                try:
                    timer.cancel()
                except Exception:
                    pass
        return None

    def _flush(self, execution_id: uuid.UUID, timer_token: object) -> None:
        with self._lock:
            state = self._active.get(execution_id)
            if state is None or state.terminal or state.pending_timer_token is not timer_token:
                return
            state.pending_timer = None
            state.pending_timer_token = None
            now = self._clock()
            state.last_emit = now
            delivery = self._delivery_locked(self._snapshot_locked(state, now))
        self._deliver(delivery)

    def _delivery_locked(
        self,
        snapshot: LiveGenerationSnapshot,
    ) -> tuple[EventSender, dict[str, Any], str] | None:
        client_id = snapshot.identity.client_id
        if self._sender is None or client_id is None:
            return None
        return self._sender, snapshot.as_dict(), client_id

    @staticmethod
    def _deliver(delivery: tuple[EventSender, dict[str, Any], str] | None) -> None:
        if delivery is None:
            return
        sender, payload, client_id = delivery
        try:
            sender(LIVE_EVENT, payload, client_id)
        except Exception:
            # Observability is fail-open.  Integrations own logging because this
            # pure layer has no safe backend details to expose.
            return

    @staticmethod
    def _cancel_timer_locked(state: _LiveExecution) -> None:
        timer = state.pending_timer
        state.pending_timer = None
        state.pending_timer_token = None
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass

    @staticmethod
    def _uuid(value: uuid.UUID | str) -> uuid.UUID:
        if isinstance(value, uuid.UUID):
            if value.version != 4:
                raise ValueError("execution_id must be a UUID4")
            return value
        if not isinstance(value, str):
            raise TypeError("execution_id must be a UUID or canonical UUID string")
        parsed = uuid.UUID(value)
        if str(parsed) != value or parsed.version != 4:
            raise ValueError("execution_id must be a canonical UUID4 string")
        return parsed

    @staticmethod
    def _validate_phase(phase: str, *, terminal: bool) -> None:
        if phase not in _PHASES:
            raise ValueError(f"unknown live generation phase: {phase}")
        if terminal != (phase in _TERMINAL_PHASES):
            kind = "terminal" if terminal else "nonterminal"
            raise ValueError(f"{phase} is not a {kind} phase")

    def close(self) -> None:
        with self._lock:
            states = tuple(self._active.values())
            self._active.clear()
            for state in states:
                self._cancel_timer_locked(state)


_DEFAULT_REGISTRY: LiveGenerationRegistry | None = None
_DEFAULT_REGISTRY_LOCK = threading.Lock()


def get_live_generation_registry() -> LiveGenerationRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        with _DEFAULT_REGISTRY_LOCK:
            if _DEFAULT_REGISTRY is None:
                _DEFAULT_REGISTRY = LiveGenerationRegistry()
    return _DEFAULT_REGISTRY


__all__ = [
    "CancelResult",
    "CancelScope",
    "CancellationToken",
    "EMIT_INTERVAL_SECONDS",
    "ExecutionIdentity",
    "LIVE_EVENT",
    "LIVE_SCHEMA_VERSION",
    "LiveGenerationCapacityError",
    "LiveGenerationHandle",
    "LiveGenerationRegistry",
    "LiveGenerationSnapshot",
    "MAX_ACTIVE_EXECUTIONS",
    "MAX_EVENT_BYTES",
    "MAX_PREVIEW_BYTES",
    "MAX_RESTORE_EXECUTIONS",
    "PreviewPane",
    "get_live_generation_registry",
]
