"""SSE parsing and bounded streaming for llama-server."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

import requests

from .client import (
    CancelCheck,
    ConnectionConfig,
    Deadline,
    DeadlineExceeded,
    LlamaClientError,
    ModelState,
    redact_secrets,
)

Line = str | bytes
ChunkCallback = Callable[[str, str], None]


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


class _StreamCancelled(Exception):
    pass


def _decode_line(line: Line) -> str:
    return line.decode("utf-8", errors="replace") if isinstance(line, bytes) else line


def iter_sse_events(
    lines: Iterable[Line],
    *,
    check: Callable[[], None] | None = None,
) -> Iterator[SSEEvent]:
    """Parse an SSE line stream, including comments and multi-line data."""

    data_lines: list[str] = []
    event_type: str | None = None
    event_id: str | None = None
    retry: int | None = None

    def dispatch() -> SSEEvent | None:
        nonlocal data_lines, event_type, event_id, retry
        if not data_lines and event_type is None and event_id is None and retry is None:
            return None
        event = SSEEvent("\n".join(data_lines), event_type, event_id, retry)
        data_lines = []
        event_type = None
        event_id = None
        retry = None
        return event

    for raw_line in lines:
        if check is not None:
            check()
        line = _decode_line(raw_line).rstrip("\r\n")
        if line == "":
            event = dispatch()
            if event is not None:
                yield event
            continue
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
) -> dict[str, Any]:
    if read_timeout is not None and read_timeout <= 0:
        raise ValueError("stream read timeout must be positive")
    kwargs: dict[str, Any] = {
        "headers": connection.request_headers(),
        "timeout": deadline.request_timeout(
            connection.connect_timeout,
            connection.read_timeout if read_timeout is None else read_timeout,
        ),
        "verify": connection.tls.verify,
        "stream": True,
    }
    if connection.tls.cert is not None:
        kwargs["cert"] = connection.tls.cert
    return kwargs


def _response_error(response: Any, connection: ConnectionConfig) -> LlamaClientError:
    text = getattr(response, "text", "") or ""
    message: str | None = None
    try:
        payload = response.json()
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = None
    if isinstance(payload, Mapping):
        error = payload.get("error")
        if isinstance(error, Mapping) and isinstance(error.get("message"), str):
            message = error["message"]
        elif isinstance(error, str):
            message = error
    detail = message or text.strip() or "stream request failed"
    rendered = redact_secrets(f"HTTP {response.status_code}: {detail[:500]}", connection.secrets)
    return LlamaClientError(
        rendered,
        status_code=int(response.status_code),
        body=redact_secrets(text[:1000], connection.secrets),
    )


def _check_stream(deadline: Deadline, cancel: CancelCheck | None, operation: str) -> None:
    if cancel is not None and cancel():
        raise _StreamCancelled()
    deadline.raise_if_expired(operation)


def iter_model_events(
    connection: ConnectionConfig,
    *,
    timeout: float | None = None,
    deadline: Deadline | None = None,
    read_timeout: float | None = None,
    cancel: CancelCheck | None = None,
    session: requests.Session | None = None,
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

        for event in iter_sse_events(response.iter_lines(decode_unicode=True), check=check):
            if not event.data:
                continue
            try:
                payload = json.loads(event.data)
            except json.JSONDecodeError as exc:
                raise LlamaClientError(
                    f"invalid JSON from /models/sse: {event.data[:200]}"
                ) from exc
            if not isinstance(payload, Mapping):
                raise LlamaClientError("/models/sse event must be a JSON object")
            model = payload.get("model")
            event_name = payload.get("event") or event.event
            if not isinstance(model, str) or not isinstance(event_name, str):
                raise LlamaClientError("/models/sse event is missing model or event")
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


def _parse_error_message(payload: Mapping[str, Any]) -> str | None:
    error = payload.get("error")
    if isinstance(error, Mapping):
        message = error.get("message")
        return message if isinstance(message, str) else str(error)
    if error is not None:
        return str(error)
    return None


def stream_chat(
    connection: ConnectionConfig,
    payload: Mapping[str, Any],
    *,
    endpoint: str = "/v1/chat/completions",
    timeout: float | None = None,
    deadline: Deadline | None = None,
    chunk_timeout: float | None = None,
    on_chunk: ChunkCallback | None = None,
    cancel: CancelCheck | None = None,
    session: requests.Session | None = None,
) -> StreamResult:
    """Consume an OpenAI-style chat SSE stream with a monotonic deadline.

    A stream is successful only after ``[DONE]`` or a choice with a non-null
    ``finish_reason``.  Content received before an error, cancellation, or
    unterminated EOF is returned with ``partial=True``.
    """

    active = _deadline_for(connection, timeout, deadline)
    owned_session = session is None
    http = session or requests.Session()
    response_obj: Any = None

    content_parts: list[str] = []
    thinking_parts: list[str] = []
    finish_reason: str | None = None
    usage: Mapping[str, Any] | None = None
    response_model: str | None = None
    response_id: str | None = None
    done_received = False
    chunks = 0

    def result(
        *,
        success: bool,
        error_message: str | None = None,
        error_type: str | None = None,
        status_code: int | None = None,
        cancelled: bool = False,
    ) -> StreamResult:
        response_text = "".join(content_parts).strip()
        thinking_text = "".join(thinking_parts).strip()
        has_partial = bool(response_text or thinking_text) and not success
        return StreamResult(
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
        )

    try:
        _check_stream(active, cancel, "chat stream")
        kwargs = _request_kwargs(connection, active, read_timeout=chunk_timeout)
        kwargs["json"] = dict(payload)
        response_obj = http.request("POST", connection.url(endpoint), **kwargs)
        if response_obj.status_code != 200:
            error = _response_error(response_obj, connection)
            return result(
                success=False,
                error_message=str(error),
                error_type="http",
                status_code=error.status_code,
            )

        def check() -> None:
            _check_stream(active, cancel, "chat stream")

        for event in iter_sse_events(response_obj.iter_lines(decode_unicode=True), check=check):
            data = event.data.strip()
            if not data:
                continue
            if data == "[DONE]":
                done_received = True
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                return result(
                    success=False,
                    error_message=f"invalid JSON stream chunk: {data[:200]}",
                    error_type="protocol",
                )
            if not isinstance(chunk, Mapping):
                return result(
                    success=False,
                    error_message="stream chunk must be a JSON object",
                    error_type="protocol",
                )
            chunks += 1

            error_message = _parse_error_message(chunk)
            if error_message is not None:
                return result(
                    success=False,
                    error_message=redact_secrets(error_message, connection.secrets),
                    error_type="server",
                )

            if isinstance(chunk.get("id"), str):
                response_id = chunk["id"]
            if isinstance(chunk.get("model"), str):
                response_model = chunk["model"]
            if isinstance(chunk.get("usage"), Mapping):
                usage = dict(chunk["usage"])

            choices = chunk.get("choices")
            if not isinstance(choices, list) or not choices:
                continue
            choice = choices[0]
            if not isinstance(choice, Mapping):
                continue
            candidate_finish = choice.get("finish_reason")
            if candidate_finish is not None:
                finish_reason = str(candidate_finish)
            delta = choice.get("delta")
            if not isinstance(delta, Mapping):
                continue
            content = delta.get("content")
            reasoning = delta.get("reasoning_content", delta.get("reasoning"))
            content_text = content if isinstance(content, str) else ""
            reasoning_text = reasoning if isinstance(reasoning, str) else ""
            if content_text:
                content_parts.append(content_text)
            if reasoning_text:
                thinking_parts.append(reasoning_text)
            if on_chunk is not None and (content_text or reasoning_text):
                on_chunk(content_text, reasoning_text)

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
        if response_obj is not None:
            response_obj.close()
        if owned_session:
            http.close()


__all__ = [
    "ModelEvent",
    "SSEEvent",
    "StreamResult",
    "iter_model_events",
    "iter_sse_events",
    "stream_chat",
]
