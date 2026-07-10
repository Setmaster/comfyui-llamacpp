"""Backward-compatible facade for bounded llama-server streaming."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .runtime.client import AuthConfig, ConnectionConfig, TLSConfig
from .runtime.streaming import StreamResult, stream_chat

StreamingResult = StreamResult


def check_interrupt() -> bool:
    try:
        import comfy.model_management as model_management
    except ImportError:
        return False
    try:
        model_management.throw_exception_if_processing_interrupted()
    except model_management.InterruptProcessingException:
        return True
    return False


def parse_server_error(error_text: str) -> str:
    try:
        data = json.loads(error_text)
    except (TypeError, json.JSONDecodeError):
        data = None
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            code = error.get("code")
            if message:
                return f"Server error ({code}): {message}" if code else f"Server error: {message}"
        if isinstance(error, str):
            return f"Server error: {error}"
        if isinstance(data.get("message"), str):
            return f"Server error: {data['message']}"
    rendered = str(error_text).strip()
    return f"Server error: {rendered[:500]}" if rendered else "Server error"


def _connection_from_endpoint(endpoint: str, timeout: int) -> tuple[ConnectionConfig, str]:
    parsed = urlsplit(endpoint.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("endpoint must be an absolute HTTP(S) URL")
    path = parsed.path or "/v1/chat/completions"
    base_path = (
        path[: -len("/v1/chat/completions")] if path.endswith("/v1/chat/completions") else ""
    )
    base_url = urlunsplit((parsed.scheme, parsed.netloc, base_path.rstrip("/"), "", ""))
    verify_tls = os.environ.get("LLAMACPP_TLS_VERIFY", "1").lower() not in {"0", "false", "no"}
    connection = ConnectionConfig(
        base_url=base_url,
        auth=AuthConfig(os.environ.get("LLAMACPP_API_KEY")),
        tls=TLSConfig(verify_tls),
        default_deadline=timeout,
    )
    return connection, path


def stream_generate(
    endpoint: str,
    payload: dict[str, Any],
    timeout: int = 300,
    chunk_timeout: int = 60,
    on_chunk: Callable[[str, str], None] | None = None,
) -> StreamingResult:
    try:
        connection, path = _connection_from_endpoint(endpoint, timeout)
        return stream_chat(
            connection,
            payload,
            endpoint=path,
            timeout=timeout,
            chunk_timeout=chunk_timeout,
            on_chunk=on_chunk,
            cancel=check_interrupt,
        )
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        return StreamingResult("", "", False, error_message=message, error_type="client")


__all__ = ["StreamingResult", "check_interrupt", "parse_server_error", "stream_generate"]
