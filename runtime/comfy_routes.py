"""Bounded Comfy HTTP surfaces for canonical local generation UX."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from typing import Any

if "." in __package__:
    from ..generation.profiles import ProfileValidationError, load_profiles
else:  # standalone runtime tests
    from generation.profiles import ProfileValidationError, load_profiles
from .client import LlamaClientError
from .live_generation import (
    MAX_RESTORE_EXECUTIONS,
    LiveGenerationRegistry,
    get_live_generation_registry,
)
from .manager import LlamaCppServerManager, get_server_manager

LOGGER = logging.getLogger(__name__)

PROFILES_ROUTE = "/llamacpp/profiles"
DISCOVERY_ROUTE = "/llamacpp/runtime/discovery"
ACTIVE_GENERATIONS_ROUTE = "/llamacpp/generation/active"
CANCEL_GENERATION_ROUTE = "/llamacpp/generation/cancel"

_PROFILE_RELATIVE_PATH = "comfyui-llamacpp/profiles.json"
_ROUTE_MARKER = "__comfyui_llamacpp_runtime_route__"
_MAX_CLIENT_ID_CHARS = 512
_MAX_MODEL_ID_CHARS = 4096
_MAX_EXECUTION_ID_CHARS = 36
_MAX_EXECUTION_CONTEXT_CHARS = 512
_MAX_CANCEL_BODY_BYTES = 4096


class _RequestTooLarge(ValueError):
    pass


def _existing_routes(routes: Any) -> dict[tuple[str, str], Any]:
    found: dict[tuple[str, str], Any] = {}
    try:
        iterator = iter(routes)
    except TypeError:
        return found
    for route in iterator:
        method = str(getattr(route, "method", "")).upper()
        path = str(getattr(route, "path", ""))
        if method and path:
            found[(method, path)] = route
    return found


def _query_value(request: Any, name: str) -> str:
    query = getattr(request, "query", None)
    if query is None:
        rel_url = getattr(request, "rel_url", None)
        query = getattr(rel_url, "query", None)
    if not isinstance(query, Mapping):
        return ""
    value = query.get(name, "")
    return value if isinstance(value, str) else str(value)


def _bounded_text(value: object, maximum: int) -> bool:
    if not isinstance(value, str) or not value or len(value) > maximum:
        return False
    try:
        return len(value.encode("utf-8", errors="strict")) <= maximum
    except UnicodeEncodeError:
        return False


def _error_payload(error_type: str, message: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "error": {
            "type": error_type[:128],
            "message": message[:2048],
        },
    }


def _json_response(web: Any, payload: Mapping[str, Any], *, status: int = 200) -> Any:
    return web.json_response(
        payload,
        status=status,
        headers={"Cache-Control": "no-store"},
    )


async def _bounded_json_body(request: Any) -> Any:
    content_length = getattr(request, "content_length", None)
    if (
        isinstance(content_length, int)
        and not isinstance(content_length, bool)
        and content_length > _MAX_CANCEL_BODY_BYTES
    ):
        raise _RequestTooLarge("request body exceeds its local limit")

    content = getattr(request, "content", None)
    reader = getattr(content, "read", None)
    if not callable(reader):
        return await request.json()

    encoded = bytearray()
    while len(encoded) <= _MAX_CANCEL_BODY_BYTES:
        chunk = await reader(_MAX_CANCEL_BODY_BYTES + 1 - len(encoded))
        if not chunk:
            break
        encoded.extend(chunk)
    if len(encoded) > _MAX_CANCEL_BODY_BYTES:
        raise _RequestTooLarge("request body exceeds its local limit")
    return json.loads(bytes(encoded).decode("utf-8"))


def _connected_client(prompt_server: Any, client_id: str) -> bool:
    sockets = getattr(prompt_server, "sockets", None)
    return isinstance(sockets, Mapping) and client_id in sockets


def _mark(handler: Any) -> Any:
    setattr(handler, _ROUTE_MARKER, True)
    return handler


def _register_get(routes: Any, path: str, handler: Any) -> None:
    routes.get(path)(_mark(handler))


def _register_post(routes: Any, path: str, handler: Any) -> None:
    routes.post(path)(_mark(handler))


def install_generation_routes(
    prompt_server: Any,
    web: Any,
    *,
    manager: LlamaCppServerManager | None = None,
    registry: LiveGenerationRegistry | None = None,
) -> bool:
    """Register the canonical UX routes once without replacing conflicts."""

    routes = getattr(prompt_server, "routes", None)
    if routes is None:
        return False
    registry = registry or get_live_generation_registry()
    registry.set_sender(getattr(prompt_server, "send_sync", None))

    existing = _existing_routes(routes)
    available: set[tuple[str, str]] = {
        key
        for key, route in existing.items()
        if bool(
            getattr(route, _ROUTE_MARKER, False)
            or getattr(getattr(route, "handler", None), _ROUTE_MARKER, False)
        )
    }

    profiles_key = ("GET", PROFILES_ROUTE)
    if profiles_key not in existing:

        async def profiles_handler(request: Any) -> Any:
            user_manager = getattr(prompt_server, "user_manager", None)
            resolver = getattr(user_manager, "get_request_user_filepath", None)
            if resolver is None:
                return _json_response(
                    web,
                    _error_payload(
                        "UserProfilesUnavailable",
                        "Comfy user profile storage is unavailable.",
                    ),
                    status=503,
                )
            try:
                path = resolver(
                    request,
                    _PROFILE_RELATIVE_PATH,
                    create_dir=False,
                )
                if path is None:
                    raise ProfileValidationError("Comfy user profile path is unavailable")
                profiles = await asyncio.to_thread(load_profiles, path)
                return _json_response(
                    web,
                    {
                        "schema_version": 1,
                        "profiles": [profile.as_dict() for profile in profiles],
                    },
                )
            except (KeyError, ProfileValidationError) as exc:
                LOGGER.warning("llama.cpp profile load failed (%s)", type(exc).__name__)
                message = (
                    str(exc)
                    if isinstance(exc, ProfileValidationError)
                    else "The current Comfy user could not be resolved."
                )
                return _json_response(
                    web,
                    _error_payload(type(exc).__name__, message),
                    status=400,
                )

        _register_get(routes, PROFILES_ROUTE, profiles_handler)
        available.add(profiles_key)
    elif profiles_key in available:
        available.add(profiles_key)

    discovery_key = ("GET", DISCOVERY_ROUTE)
    if discovery_key not in existing:

        async def discovery_handler(request: Any) -> Any:
            saved_model = _query_value(request, "saved_model")
            if saved_model and not _bounded_text(saved_model, _MAX_MODEL_ID_CHARS):
                return _json_response(
                    web,
                    _error_payload("InvalidRequest", "saved_model is too long."),
                    status=400,
                )
            try:
                active_manager = manager or get_server_manager()
                snapshot = await asyncio.to_thread(active_manager.discover_models, saved_model)
                return _json_response(web, snapshot.public_dict())
            except LlamaClientError as exc:
                LOGGER.warning(
                    "passive llama.cpp discovery failed (%s, status=%s)",
                    type(exc).__name__,
                    exc.status_code,
                )
                status = 401 if exc.status_code in {401, 403} else 502
                message = (
                    "llama-server rejected local discovery authentication; check the "
                    "configured API key environment variable."
                    if status == 401
                    else "Passive runtime discovery failed; inspect Comfy diagnostics."
                )
                return _json_response(
                    web,
                    _error_payload(type(exc).__name__, message),
                    status=status,
                )
            except Exception as exc:
                LOGGER.warning("passive llama.cpp discovery failed (%s)", type(exc).__name__)
                return _json_response(
                    web,
                    _error_payload(
                        type(exc).__name__,
                        "Passive runtime discovery failed; inspect Comfy diagnostics.",
                    ),
                    status=409 if isinstance(exc, RuntimeError) else 500,
                )

        _register_get(routes, DISCOVERY_ROUTE, discovery_handler)
        available.add(discovery_key)
    elif discovery_key in available:
        available.add(discovery_key)

    active_key = ("GET", ACTIVE_GENERATIONS_ROUTE)
    if active_key not in existing:

        async def active_handler(request: Any) -> Any:
            client_id = _query_value(request, "client_id")
            if not _bounded_text(client_id, _MAX_CLIENT_ID_CHARS):
                return _json_response(
                    web,
                    _error_payload("InvalidRequest", "A bounded client_id is required."),
                    status=400,
                )
            if not _connected_client(prompt_server, client_id):
                return _json_response(
                    web,
                    _error_payload("ClientNotConnected", "The Comfy client is not connected."),
                    status=403,
                )
            executions = registry.active_for_client(
                client_id,
                limit=MAX_RESTORE_EXECUTIONS,
            )
            return _json_response(
                web,
                {
                    "schema_version": 1,
                    "executions": executions,
                },
            )

        _register_get(routes, ACTIVE_GENERATIONS_ROUTE, active_handler)
        available.add(active_key)
    elif active_key in available:
        available.add(active_key)

    cancel_key = ("POST", CANCEL_GENERATION_ROUTE)
    if cancel_key not in existing:

        async def cancel_handler(request: Any) -> Any:
            client_id = _query_value(request, "client_id")
            if not _bounded_text(client_id, _MAX_CLIENT_ID_CHARS) or not _connected_client(
                prompt_server, client_id
            ):
                return _json_response(
                    web,
                    _error_payload("ClientNotConnected", "The Comfy client is not connected."),
                    status=403,
                )
            try:
                body = await _bounded_json_body(request)
            except _RequestTooLarge:
                return _json_response(
                    web,
                    _error_payload("RequestTooLarge", "Cancellation request is too large."),
                    status=413,
                )
            except Exception:
                body = None
            expected = {"execution_id", "prompt_id", "node_id"}
            if not isinstance(body, Mapping) or set(body) != expected:
                return _json_response(
                    web,
                    _error_payload(
                        "InvalidRequest",
                        "Cancellation requires only execution_id, prompt_id, and node_id.",
                    ),
                    status=400,
                )
            limits = {
                "execution_id": _MAX_EXECUTION_ID_CHARS,
                "prompt_id": _MAX_EXECUTION_CONTEXT_CHARS,
                "node_id": _MAX_EXECUTION_CONTEXT_CHARS,
            }
            if not all(_bounded_text(body[name], limits[name]) for name in expected):
                return _json_response(
                    web,
                    _error_payload("InvalidRequest", "Cancellation identity is invalid."),
                    status=400,
                )
            try:
                result = await asyncio.to_thread(
                    registry.cancel_for_client,
                    body["execution_id"],
                    client_id,
                    prompt_id=body["prompt_id"],
                    node_id=body["node_id"],
                )
            except (TypeError, ValueError):
                return _json_response(
                    web,
                    _error_payload("InvalidRequest", "execution_id must be a canonical UUID4."),
                    status=400,
                )
            status = 202 if result.accepted else 409
            if result.status == "not_active":
                status = 404
            return _json_response(
                web,
                {"schema_version": 1, **result.as_dict()},
                status=status,
            )

        _register_post(routes, CANCEL_GENERATION_ROUTE, cancel_handler)
        available.add(cancel_key)
    elif cancel_key in available:
        available.add(cancel_key)

    required = {profiles_key, discovery_key, active_key, cancel_key}
    return required <= available


__all__ = [
    "ACTIVE_GENERATIONS_ROUTE",
    "CANCEL_GENERATION_ROUTE",
    "DISCOVERY_ROUTE",
    "PROFILES_ROUTE",
    "install_generation_routes",
]
