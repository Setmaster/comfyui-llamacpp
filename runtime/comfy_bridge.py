"""Narrow, fail-open integration with ComfyUI's native free endpoint.

ComfyUI currently exposes no external-resource unload callback.  The bridge uses
aiohttp middleware at the server boundary so it observes both frontend and
headless calls without replacing Comfy model management or frontend commands.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .service import ReleaseResult, ReleaseStatus, RuntimeService, get_runtime_service

LOGGER = logging.getLogger(__name__)
FREE_PATHS = frozenset({"/free", "/api/free"})
STATUS_ROUTE = "/llamacpp/runtime/status"
RELEASE_ROUTE = "/llamacpp/runtime/release"
LIFECYCLE_EVENT = "llamacpp.lifecycle"
_MIDDLEWARE_MARKER = "__comfyui_llamacpp_free_bridge__"
_ROUTE_MARKER = "__comfyui_llamacpp_runtime_route__"


@dataclass(frozen=True)
class BridgeInstallResult:
    installed: bool
    routes_installed: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class ComfyEvictionResult:
    success: bool
    available: bool
    error: str | None = None


def evict_comfy_models() -> ComfyEvictionResult:
    """Synchronously release Comfy-managed models before local LLM allocation."""

    try:
        import comfy.model_management as model_management
    except ImportError:
        return ComfyEvictionResult(False, False, "Comfy model management is unavailable")
    try:
        model_management.unload_all_models()
        model_management.soft_empty_cache()
        return ComfyEvictionResult(True, True)
    except Exception as exc:
        LOGGER.exception("failed to evict Comfy-managed models before llama-server start")
        return ComfyEvictionResult(
            False,
            True,
            f"{type(exc).__name__}: Comfy model eviction failed",
        )


def _safe_result_payload(result: ReleaseResult) -> dict[str, Any]:
    payload = _safe_diagnostics_payload(result.as_dict())
    # Router/client exception strings are useful locally but may contain server
    # response material.  Events expose only the exception class/category.
    if payload.get("error"):
        payload["error"] = "runtime release failed; inspect server diagnostics"
    payload["event"] = "release"
    return payload


def _safe_failure_payload(source: str, exc: BaseException) -> dict[str, Any]:
    return {
        "event": "release",
        "source": source,
        "status": ReleaseStatus.FAILED.value,
        "success": False,
        "error_type": type(exc).__name__,
        "error": "runtime release bridge failed; inspect server diagnostics",
    }


def _safe_diagnostics_payload(payload: Any) -> Any:
    """Hide arbitrary backend exception text from HTTP-visible diagnostics."""

    if isinstance(payload, dict):
        safe: dict[str, Any] = {}
        for key, value in payload.items():
            if key in {"error", "last_error"} and value:
                safe[key] = "details withheld; inspect local server diagnostics"
            else:
                safe[key] = _safe_diagnostics_payload(value)
        return safe
    if isinstance(payload, (list, tuple)):
        return [_safe_diagnostics_payload(value) for value in payload]
    return payload


async def _emit_event(
    event_sender: Callable[[str, dict[str, Any]], Any] | None,
    payload: dict[str, Any],
) -> None:
    if event_sender is None:
        return
    try:
        value = event_sender(LIFECYCLE_EVENT, payload)
        if inspect.isawaitable(value):
            await value
    except Exception as exc:
        LOGGER.warning(
            "failed to emit llama.cpp lifecycle event (%s)",
            type(exc).__name__,
        )


def build_free_middleware(
    service: RuntimeService,
    *,
    event_sender: Callable[[str, dict[str, Any]], Any] | None = None,
) -> Callable[[Any, Callable[[Any], Awaitable[Any]]], Awaitable[Any]]:
    """Build the raw middleware function, independently of aiohttp imports."""

    async def free_middleware(request: Any, handler: Callable[[Any], Awaitable[Any]]) -> Any:
        # The core handler always runs first.  If it rejects the request, this
        # extension has no authority to perform the side effect independently.
        response = await handler(request)

        method = str(getattr(request, "method", "")).upper()
        path = str(getattr(request, "path", ""))
        status = int(getattr(response, "status", 200))
        if method != "POST" or path not in FREE_PATHS or not 200 <= status < 300:
            return response

        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                return response
            should_release = bool(payload.get("unload_models") or payload.get("free_memory"))
            if not should_release:
                return response

            result = await asyncio.to_thread(
                service.request_release,
                source="comfy_free",
                wait_for_coalesced=True,
            )
            headers = getattr(response, "headers", None)
            if headers is not None:
                try:
                    headers["X-ComfyUI-LlamaCpp-Release"] = result.status.value
                except Exception:
                    # The header is optional observability.  Release completion and
                    # its lifecycle event remain authoritative.
                    pass
            await _emit_event(event_sender, _safe_result_payload(result))
        except Exception as exc:
            # Native Comfy unloading already succeeded or was accepted.  Our
            # integration must never turn a custom-node failure into a core failure.
            LOGGER.warning(
                "llama.cpp release bridge failed (%s)",
                type(exc).__name__,
            )
            await _emit_event(event_sender, _safe_failure_payload("comfy_free", exc))
        return response

    setattr(free_middleware, _MIDDLEWARE_MARKER, True)
    return free_middleware


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


def _is_our_route(route: Any) -> bool:
    handler = getattr(route, "handler", None)
    return bool(getattr(route, _ROUTE_MARKER, False) or getattr(handler, _ROUTE_MARKER, False))


def _register_routes(
    prompt_server: Any,
    service: RuntimeService,
    web: Any,
    event_sender: Callable[[str, dict[str, Any]], Any] | None,
) -> bool:
    routes = getattr(prompt_server, "routes", None)
    if routes is None:
        return False
    existing = _existing_routes(routes)
    status_key = ("GET", STATUS_ROUTE)
    release_key = ("POST", RELEASE_ROUTE)
    status_available = status_key in existing and _is_our_route(existing[status_key])
    release_available = release_key in existing and _is_our_route(existing[release_key])

    if not status_available and status_key not in existing:

        async def status_handler(_request: Any) -> Any:
            return web.json_response(_safe_diagnostics_payload(service.diagnostics()))

        setattr(status_handler, _ROUTE_MARKER, True)
        routes.get(STATUS_ROUTE)(status_handler)
        status_available = True

    if not release_available and release_key not in existing:

        async def release_handler(_request: Any) -> Any:
            try:
                result = await asyncio.to_thread(
                    service.request_release,
                    source="comfy_explicit_api",
                    wait_for_coalesced=True,
                )
                await _emit_event(event_sender, _safe_result_payload(result))
                status = 202 if result.status == ReleaseStatus.DEFERRED else 200
                if result.status == ReleaseStatus.FAILED:
                    status = 500
                return web.json_response(_safe_result_payload(result), status=status)
            except Exception as exc:
                LOGGER.warning(
                    "explicit llama.cpp runtime release failed (%s)",
                    type(exc).__name__,
                )
                payload = _safe_failure_payload("comfy_explicit_api", exc)
                await _emit_event(event_sender, payload)
                return web.json_response(payload, status=500)

        setattr(release_handler, _ROUTE_MARKER, True)
        routes.post(RELEASE_ROUTE)(release_handler)
        release_available = True
    return status_available and release_available


def install_comfy_bridge(
    service: RuntimeService | None = None,
    *,
    prompt_server: Any | None = None,
    install_routes: bool = True,
    web_module: Any | None = None,
) -> BridgeInstallResult:
    """Install the middleware once, returning a diagnostic instead of raising."""

    service = service or get_runtime_service()
    try:
        if prompt_server is None:
            from server import PromptServer  # type: ignore

            prompt_server = getattr(PromptServer, "instance", None)
        if prompt_server is None:
            return BridgeInstallResult(False, reason="PromptServer is unavailable")

        app = getattr(prompt_server, "app", None)
        middlewares = getattr(app, "middlewares", None)
        if app is None or middlewares is None:
            return BridgeInstallResult(False, reason="PromptServer application is unavailable")

        if web_module is None:
            from aiohttp import web as web_module  # type: ignore

        existing = next(
            (item for item in middlewares if getattr(item, _MIDDLEWARE_MARKER, False)),
            None,
        )
        if existing is None:
            if bool(getattr(middlewares, "frozen", False)):
                return BridgeInstallResult(False, reason="PromptServer middleware list is frozen")
            raw_middleware = build_free_middleware(
                service,
                event_sender=getattr(prompt_server, "send_sync", None),
            )
            middleware = web_module.middleware(raw_middleware)
            setattr(middleware, _MIDDLEWARE_MARKER, True)
            middlewares.append(middleware)

        routes_installed = False
        if install_routes:
            routes_installed = _register_routes(
                prompt_server,
                service,
                web_module,
                getattr(prompt_server, "send_sync", None),
            )
        return BridgeInstallResult(True, routes_installed=routes_installed)
    except Exception as exc:
        LOGGER.warning(
            "llama.cpp Comfy lifecycle bridge unavailable (%s)",
            type(exc).__name__,
        )
        return BridgeInstallResult(
            False,
            reason=f"{type(exc).__name__}: bridge installation failed",
        )


__all__ = [
    "BridgeInstallResult",
    "ComfyEvictionResult",
    "FREE_PATHS",
    "LIFECYCLE_EVENT",
    "RELEASE_ROUTE",
    "STATUS_ROUTE",
    "build_free_middleware",
    "evict_comfy_models",
    "install_comfy_bridge",
]
