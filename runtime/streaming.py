"""SSE parsing and bounded streaming for llama-server."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from io import StringIO
from typing import Any

import requests

from .client import (
    ERROR_RESPONSE_MAX_BYTES,
    CancelCheck,
    ConnectionConfig,
    Deadline,
    DeadlineExceeded,
    LlamaClientError,
    ModelState,
    StreamCleanupSnapshot,
    StreamControl,
    _read_bounded_response_bytes,
    redact_secrets,
)

Line = str | bytes
ChunkCallback = Callable[[str, str], None]
LOGGER = logging.getLogger(__name__)
_MAX_SAFE_INTEGER = (1 << 53) - 1
DEFAULT_MAX_SSE_LINE_BYTES = 1024 * 1024
DEFAULT_MAX_SSE_EVENT_BYTES = 1024 * 1024
DEFAULT_MAX_SSE_EVENT_LINES = 16_384
DEFAULT_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_THINKING_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_MODEL_BYTES = 4096
DEFAULT_MAX_ID_BYTES = 1024
DEFAULT_MAX_FINISH_REASON_BYTES = 256
PROTOCOL_FAILURE_MESSAGE = "stream response violated the expected protocol"
RESOURCE_FAILURE_MESSAGE = "stream response exceeded a configured safety limit"
_IMAGE_UNSUPPORTED_ERROR_TYPE = "image_unsupported"
_IMAGE_UNSUPPORTED_STREAM_MESSAGE = "llama-server rejected image input"
_IMAGE_UNSUPPORTED_MESSAGE = (
    "image input is not supported - hint: if this is unexpected, you may need to provide the mmproj"
)
_IMAGE_UNSUPPORTED_ERROR_FIELDS = frozenset({"code", "message", "type"})
_IMAGE_UNSUPPORTED_MESSAGE_MAX_BYTES = 4096
_MISSING = object()


@dataclass(frozen=True, slots=True)
class SSEEvent:
    data: str
    event: str | None = None
    id: str | None = None
    retry: int | None = None


@dataclass(frozen=True, slots=True)
class ModelEvent:
    model: str
    event: str
    state: ModelState | None
    data: Any
    raw: Mapping[str, Any] = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class PromptProgress:
    total: int
    cached: int
    processed: int
    time_ms: int | None = None

    def __post_init__(self) -> None:
        values = (self.total, self.cached, self.processed)
        if any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            or value > _MAX_SAFE_INTEGER
            for value in values
        ):
            raise ValueError("prompt progress counters must be non-negative safe integers")
        if self.cached > self.processed or self.processed > self.total:
            raise ValueError("prompt progress must satisfy cached <= processed <= total")
        if self.time_ms is not None and (
            isinstance(self.time_ms, bool)
            or not isinstance(self.time_ms, int)
            or self.time_ms < 0
            or self.time_ms > _MAX_SAFE_INTEGER
        ):
            raise ValueError("prompt progress time must be a non-negative safe integer or None")

    @property
    def percent(self) -> float | None:
        return (self.processed / self.total) * 100.0 if self.total > 0 else None

    def as_dict(self) -> dict[str, int | float | None]:
        return {
            "total": self.total,
            "cached": self.cached,
            "processed": self.processed,
            "percent": self.percent,
            "time_ms": self.time_ms,
        }


@dataclass(frozen=True, slots=True)
class StreamUpdate:
    """One normalized observation from a streamed JSON chunk."""

    content: str = ""
    thinking: str = ""
    prompt_progress: PromptProgress | None = None
    finish_reason: str | None = None
    usage: Mapping[str, Any] | None = None
    model: str | None = None
    response_id: str | None = None
    chunk_index: int = 0


UpdateCallback = Callable[[StreamUpdate], None]


@dataclass(frozen=True, slots=True)
class StreamResult:
    response: str
    thinking: str
    success: bool
    error_message: str | None = None
    error_type: str | None = None
    status_code: int | None = None
    finish_reason: str | None = None
    usage: Mapping[str, Any] | None = None
    model: str | None = None
    response_id: str | None = None
    done_received: bool = False
    partial: bool = False
    cancelled: bool = False
    chunks: int = 0
    prompt_progress: PromptProgress | None = None
    stream_cleanup: StreamCleanupSnapshot | None = field(
        default=None,
        repr=False,
        compare=False,
    )


class _StreamCancelled(Exception):
    pass


class StreamProtocolError(LlamaClientError):
    """The streaming peer emitted a malformed SSE or JSON field."""


class StreamResourceLimit(LlamaClientError):
    """The streaming peer exceeded a bounded wire or output resource."""


class _StreamResponseError(LlamaClientError):
    """One sanitized HTTP stream failure with an optional allowlisted token."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        error_type: str | None = None,
    ) -> None:
        super().__init__(message, status_code=status_code)
        self.error_type = error_type


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _structured_error_type(
    payload: object,
    *,
    http_status: int | None,
) -> str | None:
    """Recognize only the pinned llama.cpp unsupported-image error contract."""

    if http_status is not None and http_status != 400:
        return None
    if not isinstance(payload, Mapping) or set(payload) != {"error"}:
        return None
    error = payload.get("error")
    if not isinstance(error, Mapping) or set(error) != _IMAGE_UNSUPPORTED_ERROR_FIELDS:
        return None
    code = error.get("code")
    error_type = error.get("type")
    message = error.get("message")
    if type(code) is not int or code != 400:
        return None
    if error_type != "invalid_request_error" or not isinstance(message, str):
        return None
    try:
        encoded = message.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return None
    if len(encoded) > _IMAGE_UNSUPPORTED_MESSAGE_MAX_BYTES:
        return None
    if message != _IMAGE_UNSUPPORTED_MESSAGE:
        return None
    return _IMAGE_UNSUPPORTED_ERROR_TYPE


def _structured_error_bytes_type(
    body: bytes,
    *,
    http_status: int,
    truncated: bool,
) -> str | None:
    if truncated or not body:
        return None
    try:
        text = body.decode("utf-8", errors="strict")
        payload = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError, RecursionError):
        return None
    return _structured_error_type(payload, http_status=http_status)


def _validate_limit(name: str, value: int | None) -> None:
    if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value <= 0):
        raise ValueError(f"{name} must be a positive integer or None")


def _decode_line(line: Line, *, strict_utf8: bool) -> tuple[str, int]:
    errors = "strict" if strict_utf8 else "replace"
    try:
        if isinstance(line, bytes):
            return line.decode("utf-8", errors=errors), len(line)
        encoded = line.encode("utf-8", errors=errors)
        return line, len(encoded)
    except (UnicodeDecodeError, UnicodeEncodeError) as exc:
        raise StreamProtocolError(PROTOCOL_FAILURE_MESSAGE) from exc


def iter_sse_events(
    lines: Iterable[Line],
    *,
    check: Callable[[], None] | None = None,
    max_line_bytes: int | None = DEFAULT_MAX_SSE_LINE_BYTES,
    max_event_bytes: int | None = DEFAULT_MAX_SSE_EVENT_BYTES,
    max_event_lines: int | None = DEFAULT_MAX_SSE_EVENT_LINES,
    strict_utf8: bool = False,
) -> Iterator[SSEEvent]:
    """Parse an SSE line stream, including comments and multi-line data."""

    _validate_limit("max_line_bytes", max_line_bytes)
    _validate_limit("max_event_bytes", max_event_bytes)
    _validate_limit("max_event_lines", max_event_lines)

    data_lines: list[str] = []
    event_type: str | None = None
    event_id: str | None = None
    retry: int | None = None
    event_bytes = 0
    event_lines = 0

    def dispatch() -> SSEEvent | None:
        nonlocal data_lines, event_type, event_id, retry, event_bytes, event_lines
        if not data_lines and event_type is None and event_id is None and retry is None:
            event_bytes = 0
            event_lines = 0
            return None
        event = SSEEvent("\n".join(data_lines), event_type, event_id, retry)
        data_lines = []
        event_type = None
        event_id = None
        retry = None
        event_bytes = 0
        event_lines = 0
        return event

    for raw_line in lines:
        if check is not None:
            check()
        line, line_bytes = _decode_line(raw_line, strict_utf8=strict_utf8)
        if max_line_bytes is not None and line_bytes > max_line_bytes:
            raise StreamResourceLimit(RESOURCE_FAILURE_MESSAGE)
        line = line.rstrip("\r\n")
        if line == "":
            event = dispatch()
            if event is not None:
                yield event
            continue
        event_bytes += line_bytes + 1
        event_lines += 1
        if max_event_bytes is not None and event_bytes > max_event_bytes:
            raise StreamResourceLimit(RESOURCE_FAILURE_MESSAGE)
        if max_event_lines is not None and event_lines > max_event_lines:
            raise StreamResourceLimit(RESOURCE_FAILURE_MESSAGE)
        if line.startswith(":"):
            continue

        field_name, separator, value = line.partition(":")
        if not separator:
            value = ""
        elif value.startswith(" "):
            value = value[1:]
        if field_name == "data":
            data_lines.append(value)
        elif field_name == "event":
            event_type = value
        elif field_name == "id":
            event_id = value
        elif field_name == "retry":
            try:
                retry = int(value)
            except ValueError:
                pass

    if check is not None:
        check()
    event = dispatch()
    if event is not None:
        yield event


def _iter_response_lines(
    response: Any,
    *,
    max_line_bytes: int,
    check: Callable[[], None] | None = None,
) -> Iterator[bytes]:
    """Split response bytes without allowing ``requests.iter_lines`` to grow unchecked."""

    _validate_limit("max_line_bytes", max_line_bytes)
    line = bytearray()
    pending_cr = False
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if check is not None:
            check()
        if not chunk:
            continue
        if isinstance(chunk, str):
            try:
                encoded = chunk.encode("utf-8", errors="strict")
            except UnicodeEncodeError as exc:
                raise StreamProtocolError(PROTOCOL_FAILURE_MESSAGE) from exc
        elif isinstance(chunk, (bytes, bytearray, memoryview)):
            encoded = bytes(chunk)
        else:
            raise StreamProtocolError(PROTOCOL_FAILURE_MESSAGE)

        for value in encoded:
            if pending_cr:
                pending_cr = False
                yield bytes(line)
                line.clear()
                if value == 0x0A:
                    continue
            if value == 0x0D:
                pending_cr = True
            elif value == 0x0A:
                yield bytes(line)
                line.clear()
            else:
                if len(line) >= max_line_bytes:
                    raise StreamResourceLimit(RESOURCE_FAILURE_MESSAGE)
                line.append(value)
    if check is not None:
        check()
    if pending_cr:
        yield bytes(line)
    elif line:
        yield bytes(line)


def _deadline_for(
    connection: ConnectionConfig, timeout: float | None, deadline: Deadline | None
) -> Deadline:
    if deadline is not None:
        return deadline
    duration = connection.default_deadline if timeout is None else timeout
    return Deadline(duration)


def _request_kwargs(
    connection: ConnectionConfig,
    deadline: Deadline,
    *,
    read_timeout: float | None = None,
    extra_headers: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    if read_timeout is not None and read_timeout <= 0:
        raise ValueError("stream read timeout must be positive")
    headers = connection.request_headers()
    for key in tuple(headers):
        if key.lower() == "x-conversation-id":
            headers.pop(key)
    if extra_headers:
        headers.update(extra_headers)
    kwargs: dict[str, Any] = {
        "headers": headers,
        "timeout": deadline.request_timeout(
            connection.connect_timeout,
            connection.read_timeout if read_timeout is None else read_timeout,
        ),
        "verify": connection.tls.verify,
        "stream": True,
        "allow_redirects": False,
    }
    if connection.tls.cert is not None:
        kwargs["cert"] = connection.tls.cert
    return kwargs


def _response_error(
    response: Any,
    connection: ConnectionConfig,
    *,
    classify_image_unsupported: bool = False,
) -> LlamaClientError:
    status_code = int(response.status_code)
    error_type: str | None = None
    try:
        body, truncated = _read_bounded_response_bytes(
            response,
            ERROR_RESPONSE_MAX_BYTES,
            truncate=True,
        )
        if classify_image_unsupported:
            error_type = _structured_error_bytes_type(
                body,
                http_status=status_code,
                truncated=truncated,
            )
    except (LlamaClientError, requests.RequestException):
        pass
    return _StreamResponseError(
        f"HTTP {status_code}: stream request failed",
        status_code=status_code,
        error_type=error_type,
    )


def _check_stream(deadline: Deadline, cancel: CancelCheck | None, operation: str) -> None:
    if cancel is not None and cancel():
        raise _StreamCancelled()
    deadline.raise_if_expired(operation)


def _strict_optional_string(
    value: object,
    *,
    max_bytes: int,
    required: bool = False,
) -> str | None:
    if value is _MISSING or value is None:
        if required:
            raise StreamProtocolError(PROTOCOL_FAILURE_MESSAGE)
        return None
    if not isinstance(value, str):
        raise StreamProtocolError(PROTOCOL_FAILURE_MESSAGE)
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise StreamProtocolError(PROTOCOL_FAILURE_MESSAGE) from exc
    if len(encoded) > max_bytes:
        raise StreamResourceLimit(RESOURCE_FAILURE_MESSAGE)
    return value


def iter_model_events(
    connection: ConnectionConfig,
    *,
    timeout: float | None = None,
    deadline: Deadline | None = None,
    read_timeout: float | None = None,
    cancel: CancelCheck | None = None,
    session: requests.Session | None = None,
    max_line_bytes: int = DEFAULT_MAX_SSE_LINE_BYTES,
    max_event_bytes: int = DEFAULT_MAX_SSE_EVENT_BYTES,
) -> Iterator[ModelEvent]:
    """Yield typed events from the protected ``/models/sse`` endpoint."""

    active = _deadline_for(connection, timeout, deadline)
    owned_session = session is None
    http = session or requests.Session()
    response: Any = None
    try:
        _check_stream(active, cancel, "model event stream")
        response = http.request(
            "GET",
            connection.url("/models/sse"),
            **_request_kwargs(connection, active, read_timeout=read_timeout),
        )
        if response.status_code != 200:
            raise _response_error(response, connection)

        def check() -> None:
            _check_stream(active, cancel, "model event stream")

        lines = _iter_response_lines(
            response,
            max_line_bytes=max_line_bytes,
            check=check,
        )
        for event in iter_sse_events(
            lines,
            check=check,
            max_line_bytes=max_line_bytes,
            max_event_bytes=max_event_bytes,
            max_event_lines=DEFAULT_MAX_SSE_EVENT_LINES,
            strict_utf8=True,
        ):
            if not event.data:
                continue
            try:
                payload = json.loads(event.data)
            except json.JSONDecodeError as exc:
                raise StreamProtocolError(PROTOCOL_FAILURE_MESSAGE) from exc
            if not isinstance(payload, Mapping):
                raise StreamProtocolError(PROTOCOL_FAILURE_MESSAGE)
            model = _strict_optional_string(
                payload.get("model", _MISSING),
                max_bytes=DEFAULT_MAX_MODEL_BYTES,
                required=True,
            )
            event_name = _strict_optional_string(
                payload.get("event") or event.event or _MISSING,
                max_bytes=DEFAULT_MAX_MODEL_BYTES,
                required=True,
            )
            assert model is not None and event_name is not None
            data = payload.get("data")
            status_value = data.get("status") if isinstance(data, Mapping) else None
            failed = bool(
                isinstance(data, Mapping)
                and status_value == "unloaded"
                and isinstance(data.get("exit_code"), int)
                and data["exit_code"] != 0
            )
            state = (
                ModelState.from_upstream(status_value, failed=failed)
                if status_value is not None
                else None
            )
            yield ModelEvent(model, event_name, state, data, dict(payload))
    except _StreamCancelled:
        return
    except requests.Timeout as exc:
        raise DeadlineExceeded("model event stream timed out") from exc
    except requests.RequestException as exc:
        raise LlamaClientError(
            redact_secrets(f"model event stream failed: {exc}", connection.secrets)
        ) from exc
    finally:
        if response is not None:
            response.close()
        if owned_session:
            http.close()


def _parse_stream_error(
    payload: Mapping[str, Any],
    *,
    classify_image_unsupported: bool = False,
) -> tuple[str, str] | None:
    if payload.get("error") is None:
        return None
    if classify_image_unsupported:
        error_type = _structured_error_type(payload, http_status=None)
        if error_type == _IMAGE_UNSUPPORTED_ERROR_TYPE:
            return _IMAGE_UNSUPPORTED_STREAM_MESSAGE, _IMAGE_UNSUPPORTED_ERROR_TYPE
    return "server returned an error", "server"


def _nonnegative_int(value: object) -> int | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > _MAX_SAFE_INTEGER
    ):
        return None
    return value


def _parse_prompt_progress(value: object) -> PromptProgress | None:
    if not isinstance(value, Mapping):
        return None
    total = _nonnegative_int(value.get("total"))
    cached = _nonnegative_int(value.get("cache"))
    processed = _nonnegative_int(value.get("processed"))
    if total is None or cached is None or processed is None:
        return None
    if cached > processed or processed > total:
        return None
    time_ms = _nonnegative_int(value.get("time_ms"))
    return PromptProgress(total, cached, processed, time_ms)


def _notify_update(callback: UpdateCallback | None, update: StreamUpdate) -> None:
    if callback is None:
        return
    try:
        callback(update)
    except Exception as exc:
        # Live observability is additive and must not turn a valid generation
        # into a workflow failure.  BaseException still propagates so Comfy's
        # interrupt signal retains its established behavior.
        LOGGER.warning("stream update callback failed (%s)", type(exc).__name__)


def stream_chat(
    connection: ConnectionConfig,
    payload: Mapping[str, Any],
    *,
    endpoint: str = "/v1/chat/completions",
    timeout: float | None = None,
    deadline: Deadline | None = None,
    chunk_timeout: float | None = None,
    on_chunk: ChunkCallback | None = None,
    on_update: UpdateCallback | None = None,
    cancel: CancelCheck | None = None,
    stream_control: StreamControl | None = None,
    session: requests.Session | None = None,
    strict_protocol: bool = False,
    max_line_bytes: int | None = DEFAULT_MAX_SSE_LINE_BYTES,
    max_event_bytes: int | None = DEFAULT_MAX_SSE_EVENT_BYTES,
    max_response_bytes: int | None = DEFAULT_MAX_RESPONSE_BYTES,
    max_thinking_bytes: int | None = DEFAULT_MAX_THINKING_BYTES,
    max_model_bytes: int | None = None,
    max_id_bytes: int | None = None,
    max_finish_reason_bytes: int | None = None,
) -> StreamResult:
    """Consume an OpenAI-style chat SSE stream with a monotonic deadline.

    A stream is successful only after ``[DONE]`` or a choice with a non-null
    ``finish_reason``.  Content received before an error, cancellation, or
    unterminated EOF is returned with ``partial=True``.
    """

    if strict_protocol:
        max_line_bytes = DEFAULT_MAX_SSE_LINE_BYTES if max_line_bytes is None else max_line_bytes
        max_event_bytes = (
            DEFAULT_MAX_SSE_EVENT_BYTES if max_event_bytes is None else max_event_bytes
        )
        max_response_bytes = (
            DEFAULT_MAX_RESPONSE_BYTES if max_response_bytes is None else max_response_bytes
        )
        max_thinking_bytes = (
            DEFAULT_MAX_THINKING_BYTES if max_thinking_bytes is None else max_thinking_bytes
        )
        max_model_bytes = DEFAULT_MAX_MODEL_BYTES if max_model_bytes is None else max_model_bytes
        max_id_bytes = DEFAULT_MAX_ID_BYTES if max_id_bytes is None else max_id_bytes
        max_finish_reason_bytes = (
            DEFAULT_MAX_FINISH_REASON_BYTES
            if max_finish_reason_bytes is None
            else max_finish_reason_bytes
        )
    for limit_name, limit_value in (
        ("max_line_bytes", max_line_bytes),
        ("max_event_bytes", max_event_bytes),
        ("max_response_bytes", max_response_bytes),
        ("max_thinking_bytes", max_thinking_bytes),
        ("max_model_bytes", max_model_bytes),
        ("max_id_bytes", max_id_bytes),
        ("max_finish_reason_bytes", max_finish_reason_bytes),
    ):
        _validate_limit(limit_name, limit_value)

    active = _deadline_for(connection, timeout, deadline)
    owned_session = session is None
    http = session or requests.Session()
    response_obj: Any = None

    content_buffer = StringIO()
    thinking_buffer = StringIO()
    content_bytes = 0
    thinking_bytes = 0
    finish_reason: str | None = None
    usage: Mapping[str, Any] | None = None
    response_model: str | None = None
    response_id: str | None = None
    done_received = False
    chunks = 0
    prompt_progress: PromptProgress | None = None
    completed_result: StreamResult | None = None

    def result(
        *,
        success: bool,
        error_message: str | None = None,
        error_type: str | None = None,
        status_code: int | None = None,
        cancelled: bool = False,
    ) -> StreamResult:
        nonlocal completed_result
        # Model output is a byte-for-byte workflow value once each decoded SSE
        # delta reaches us. Leading and trailing whitespace can be meaningful
        # for code, templates, and downstream string composition, so never
        # normalize the assembled response here.
        response_text = content_buffer.getvalue()
        thinking_text = thinking_buffer.getvalue()
        has_partial = bool(response_text or thinking_text) and not success
        completed_result = StreamResult(
            response=response_text,
            thinking=thinking_text,
            success=success,
            error_message=error_message,
            error_type=error_type,
            status_code=status_code,
            finish_reason=finish_reason,
            usage=usage,
            model=response_model,
            response_id=response_id,
            done_received=done_received,
            partial=has_partial,
            cancelled=cancelled,
            chunks=chunks,
            prompt_progress=prompt_progress,
        )
        return completed_result

    def append_output(
        buffer: StringIO,
        value: str,
        current_bytes: int,
        limit: int | None,
    ) -> int:
        if not value:
            return current_bytes
        if strict_protocol or limit is not None:
            try:
                encoded = value.encode("utf-8", errors="strict")
            except UnicodeEncodeError as exc:
                raise StreamProtocolError(PROTOCOL_FAILURE_MESSAGE) from exc
            if limit is not None and len(encoded) > limit - current_bytes:
                raise StreamResourceLimit(RESOURCE_FAILURE_MESSAGE)
            current_bytes += len(encoded)
        buffer.write(value)
        return current_bytes

    try:
        _check_stream(active, cancel, "chat stream")
        request_payload = dict(payload)
        extra_headers: dict[str, str] | None = None
        if stream_control is not None:
            if stream_control.connection != connection:
                raise ValueError("stream control connection does not match the chat connection")
            request_payload["return_progress"] = True
            request_payload["sse_ping_interval"] = 1
            extra_headers = {"X-Conversation-Id": str(stream_control.conversation_id)}
        kwargs = _request_kwargs(
            connection,
            active,
            read_timeout=chunk_timeout,
            extra_headers=extra_headers,
        )
        kwargs["json"] = request_payload
        response_obj = http.request("POST", connection.url(endpoint), **kwargs)
        if response_obj.status_code != 200:
            error = _response_error(
                response_obj,
                connection,
                classify_image_unsupported=strict_protocol,
            )
            classified_error = getattr(error, "error_type", None)
            return result(
                success=False,
                error_message=(
                    _IMAGE_UNSUPPORTED_STREAM_MESSAGE
                    if classified_error == _IMAGE_UNSUPPORTED_ERROR_TYPE
                    else str(error)
                ),
                # Canonical strict generation is calling the chat-completions
                # endpoint for one already selected model. llama.cpp redacts
                # the useful body here, so status plus endpoint context is the
                # only stable missing-model signal. Keep legacy classification
                # unchanged.
                error_type=(
                    _IMAGE_UNSUPPORTED_ERROR_TYPE
                    if classified_error == _IMAGE_UNSUPPORTED_ERROR_TYPE
                    else (
                        "model_missing"
                        if strict_protocol
                        and endpoint == "/v1/chat/completions"
                        and error.status_code == 404
                        else "http"
                    )
                ),
                status_code=error.status_code,
            )

        def check() -> None:
            _check_stream(active, cancel, "chat stream")

        if max_line_bytes is None:
            lines: Iterable[Line] = response_obj.iter_lines(decode_unicode=False)
        else:
            lines = _iter_response_lines(
                response_obj,
                max_line_bytes=max_line_bytes,
                check=check,
            )
        for event in iter_sse_events(
            lines,
            check=check,
            max_line_bytes=max_line_bytes,
            max_event_bytes=max_event_bytes,
            max_event_lines=DEFAULT_MAX_SSE_EVENT_LINES if max_event_bytes is not None else None,
            strict_utf8=strict_protocol,
        ):
            data = event.data.strip()
            if not data:
                continue
            if data == "[DONE]":
                done_received = True
                break
            try:
                chunk = json.loads(
                    data,
                    object_pairs_hook=_reject_duplicate_json_keys,
                    parse_constant=_reject_json_constant,
                )
            except (ValueError, json.JSONDecodeError, RecursionError):
                return result(
                    success=False,
                    error_message=PROTOCOL_FAILURE_MESSAGE,
                    error_type="protocol",
                )
            if not isinstance(chunk, Mapping):
                return result(
                    success=False,
                    error_message="stream chunk must be a JSON object",
                    error_type="protocol",
                )
            chunks += 1

            stream_error = _parse_stream_error(
                chunk,
                classify_image_unsupported=strict_protocol,
            )
            if stream_error is not None:
                error_message, error_type = stream_error
                return result(
                    success=False,
                    error_message=redact_secrets(error_message, connection.secrets),
                    error_type=error_type,
                )

            if strict_protocol:
                candidate_id = _strict_optional_string(
                    chunk.get("id", _MISSING),
                    max_bytes=max_id_bytes or DEFAULT_MAX_ID_BYTES,
                )
                candidate_model = _strict_optional_string(
                    chunk.get("model", _MISSING),
                    max_bytes=max_model_bytes or DEFAULT_MAX_MODEL_BYTES,
                )
                if candidate_id is not None:
                    if response_id is None:
                        response_id = candidate_id
                    elif candidate_id != response_id:
                        raise StreamProtocolError(PROTOCOL_FAILURE_MESSAGE)
                if candidate_model is not None:
                    if response_model is None:
                        response_model = candidate_model
                    elif candidate_model != response_model:
                        raise StreamProtocolError(PROTOCOL_FAILURE_MESSAGE)
            else:
                if isinstance(chunk.get("id"), str):
                    response_id = chunk["id"]
                if isinstance(chunk.get("model"), str):
                    response_model = chunk["model"]

            candidate_usage = chunk.get("usage", _MISSING)
            if isinstance(candidate_usage, Mapping):
                usage = dict(candidate_usage)
            elif (
                strict_protocol and candidate_usage is not _MISSING and candidate_usage is not None
            ):
                raise StreamProtocolError(PROTOCOL_FAILURE_MESSAGE)
            candidate_progress = _parse_prompt_progress(chunk.get("prompt_progress"))
            if candidate_progress is not None:
                prompt_progress = candidate_progress

            choices = chunk.get("choices", _MISSING)
            content_text = ""
            reasoning_text = ""
            if isinstance(choices, list) and choices:
                choice = choices[0]
                if isinstance(choice, Mapping):
                    candidate_finish = choice.get("finish_reason", _MISSING)
                    if strict_protocol:
                        parsed_finish = _strict_optional_string(
                            candidate_finish,
                            max_bytes=max_finish_reason_bytes or DEFAULT_MAX_FINISH_REASON_BYTES,
                        )
                        if parsed_finish is not None:
                            finish_reason = parsed_finish
                    elif candidate_finish is not _MISSING and candidate_finish is not None:
                        finish_reason = str(candidate_finish)
                    delta = choice.get("delta", _MISSING)
                    if isinstance(delta, Mapping):
                        content = delta.get("content", _MISSING)
                        reasoning = (
                            delta.get("reasoning_content")
                            if "reasoning_content" in delta
                            else delta.get("reasoning", _MISSING)
                        )
                        if strict_protocol:
                            content_text = (
                                _strict_optional_string(
                                    content,
                                    max_bytes=max_response_bytes or DEFAULT_MAX_RESPONSE_BYTES,
                                )
                                or ""
                            )
                            reasoning_text = (
                                _strict_optional_string(
                                    reasoning,
                                    max_bytes=max_thinking_bytes or DEFAULT_MAX_THINKING_BYTES,
                                )
                                or ""
                            )
                        else:
                            content_text = content if isinstance(content, str) else ""
                            reasoning_text = reasoning if isinstance(reasoning, str) else ""
                    elif strict_protocol and delta is not _MISSING and delta is not None:
                        raise StreamProtocolError(PROTOCOL_FAILURE_MESSAGE)
                elif strict_protocol:
                    raise StreamProtocolError(PROTOCOL_FAILURE_MESSAGE)
            elif (
                strict_protocol
                and choices is not _MISSING
                and choices is not None
                and not isinstance(choices, list)
            ):
                raise StreamProtocolError(PROTOCOL_FAILURE_MESSAGE)
            if content_text:
                content_bytes = append_output(
                    content_buffer,
                    content_text,
                    content_bytes,
                    max_response_bytes,
                )
            if reasoning_text:
                thinking_bytes = append_output(
                    thinking_buffer,
                    reasoning_text,
                    thinking_bytes,
                    max_thinking_bytes,
                )
            if on_chunk is not None and (content_text or reasoning_text):
                on_chunk(content_text, reasoning_text)
            if (
                content_text
                or reasoning_text
                or candidate_progress is not None
                or finish_reason is not None
                or usage is not None
                or response_model is not None
                or response_id is not None
            ):
                _notify_update(
                    on_update,
                    StreamUpdate(
                        content=content_text,
                        thinking=reasoning_text,
                        prompt_progress=candidate_progress,
                        finish_reason=finish_reason,
                        usage=usage,
                        model=response_model,
                        response_id=response_id,
                        chunk_index=chunks,
                    ),
                )

        terminal = done_received or finish_reason is not None
        if terminal:
            return result(success=True)
        return result(
            success=False,
            error_message="stream ended before [DONE] or finish_reason",
            error_type="incomplete",
        )
    except _StreamCancelled:
        return result(
            success=False,
            error_message="generation cancelled",
            error_type="cancelled",
            cancelled=True,
        )
    except StreamResourceLimit:
        return result(
            success=False,
            error_message=RESOURCE_FAILURE_MESSAGE,
            error_type="resource",
        )
    except StreamProtocolError:
        return result(
            success=False,
            error_message=PROTOCOL_FAILURE_MESSAGE,
            error_type="protocol",
        )
    except DeadlineExceeded as exc:
        return result(success=False, error_message=str(exc), error_type="timeout")
    except requests.Timeout as exc:
        return result(
            success=False,
            error_message=redact_secrets(f"stream timeout: {exc}", connection.secrets),
            error_type="timeout",
        )
    except requests.RequestException as exc:
        return result(
            success=False,
            error_message=redact_secrets(f"stream transport error: {exc}", connection.secrets),
            error_type="transport",
        )
    finally:
        try:
            if stream_control is not None:
                try:
                    # With X-Conversation-Id, closing the response deliberately
                    # does not stop llama.cpp.  DELETE is mandatory on every exit,
                    # including BaseException paths such as Comfy interruption.
                    confirmed = stream_control.delete()
                    if not confirmed:
                        LOGGER.warning("stream replay cleanup was not confirmed")
                except Exception as exc:
                    LOGGER.warning(
                        "stream replay cleanup failed (%s)",
                        type(exc).__name__,
                    )
        finally:
            try:
                if response_obj is not None:
                    response_obj.close()
            except Exception as exc:
                LOGGER.warning("stream response close failed (%s)", type(exc).__name__)
            try:
                if owned_session:
                    http.close()
            except Exception as exc:
                LOGGER.warning("stream HTTP session close failed (%s)", type(exc).__name__)
            if completed_result is not None and stream_control is not None:
                object.__setattr__(
                    completed_result,
                    "stream_cleanup",
                    stream_control.cleanup.snapshot(),
                )


__all__ = [
    "DEFAULT_MAX_FINISH_REASON_BYTES",
    "DEFAULT_MAX_ID_BYTES",
    "DEFAULT_MAX_MODEL_BYTES",
    "DEFAULT_MAX_RESPONSE_BYTES",
    "DEFAULT_MAX_SSE_EVENT_BYTES",
    "DEFAULT_MAX_SSE_LINE_BYTES",
    "DEFAULT_MAX_THINKING_BYTES",
    "ModelEvent",
    "PromptProgress",
    "SSEEvent",
    "StreamResult",
    "StreamProtocolError",
    "StreamResourceLimit",
    "StreamUpdate",
    "iter_model_events",
    "iter_sse_events",
    "stream_chat",
]
