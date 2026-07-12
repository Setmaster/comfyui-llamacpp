"""Tests for the narrow, fail-open Comfy free-request bridge."""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass

import pytest

from runtime.comfy_bridge import (
    LIFECYCLE_EVENT,
    RELEASE_ROUTE,
    STATUS_ROUTE,
    build_free_middleware,
    install_comfy_bridge,
)
from runtime.comfy_routes import (
    ACTIVE_GENERATIONS_ROUTE,
    CANCEL_GENERATION_ROUTE,
    DISCOVERY_ROUTE,
    PROFILES_ROUTE,
)
from runtime.service import ReleaseResult, ReleaseStatus, RuntimeMode


class FakeRequest:
    def __init__(self, path: str, payload: object, *, method: str = "POST") -> None:
        self.path = path
        self.method = method
        self._payload = payload

    async def json(self) -> object:
        if isinstance(self._payload, BaseException):
            raise self._payload
        return self._payload


class FakeResponse:
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.headers: dict[str, str] = {}


class FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool, int]] = []
        self.failure: BaseException | None = None
        self.diagnostic_payload: dict[str, object] = {"mode": "direct"}
        self.released_model_diagnostics: tuple[dict[str, object], ...] = ()

    def request_release(
        self,
        *,
        source: str,
        wait_for_coalesced: bool,
    ) -> ReleaseResult:
        self.calls.append((source, wait_for_coalesced, threading.get_ident()))
        if self.failure is not None:
            raise self.failure
        now = time.time()
        return ReleaseResult(
            request_id="fixture-release",
            source=source,
            status=ReleaseStatus.COMPLETE,
            mode=RuntimeMode.DIRECT,
            owned=True,
            started_at=now,
            completed_at=now,
            released_model_diagnostics=self.released_model_diagnostics,
        )

    def diagnostics(self) -> dict[str, object]:
        return self.diagnostic_payload


@pytest.mark.parametrize("path", ["/free", "/api/free"])
@pytest.mark.parametrize("flag", ["unload_models", "free_memory"])
def test_successful_native_free_runs_core_first_then_releases_off_event_loop(
    path: str,
    flag: str,
) -> None:
    order: list[str] = []
    events: list[tuple[str, dict[str, object]]] = []
    service = FakeService()
    main_thread = threading.get_ident()

    async def handler(_request: FakeRequest) -> FakeResponse:
        order.append("core")
        return FakeResponse()

    def send_event(name: str, payload: dict[str, object]) -> None:
        order.append("release")
        events.append((name, payload))

    response = asyncio.run(
        build_free_middleware(service, event_sender=send_event)(
            FakeRequest(path, {flag: True}),
            handler,
        )
    )

    assert order == ["core", "release"]
    assert len(service.calls) == 1
    assert service.calls[0][:2] == ("comfy_free", True)
    assert service.calls[0][2] != main_thread
    assert response.headers["X-ComfyUI-LlamaCpp-Release"] == "complete"
    assert events[0][0] == LIFECYCLE_EVENT
    assert events[0][1]["status"] == "complete"


@pytest.mark.parametrize(
    ("request_case", "response_status"),
    [
        (FakeRequest("/not-free", {"unload_models": True}), 200),
        (FakeRequest("/free", {"unload_models": True}, method="GET"), 200),
        (FakeRequest("/free", {"unload_models": False}), 200),
        (FakeRequest("/free", []), 200),
        (FakeRequest("/free", {"free_memory": True}), 500),
        (FakeRequest("/free", {"free_memory": True}), 302),
    ],
)
def test_unrelated_rejected_or_false_requests_have_no_extension_side_effect(
    request_case: FakeRequest,
    response_status: int,
) -> None:
    service = FakeService()

    async def handler(_request: FakeRequest) -> FakeResponse:
        return FakeResponse(response_status)

    response = asyncio.run(build_free_middleware(service)(request_case, handler))

    assert response.status == response_status
    assert service.calls == []


def test_core_failure_propagates_without_attempting_extension_release() -> None:
    service = FakeService()

    async def handler(_request: FakeRequest) -> FakeResponse:
        raise RuntimeError("core failed")

    with pytest.raises(RuntimeError, match="core failed"):
        asyncio.run(
            build_free_middleware(service)(
                FakeRequest("/free", {"unload_models": True}),
                handler,
            )
        )
    assert service.calls == []


def test_extension_failure_is_fail_open_and_emits_no_raw_error_material() -> None:
    secret = "do-not-expose-this-value"
    service = FakeService()
    service.failure = RuntimeError(secret)
    events: list[tuple[str, dict[str, object]]] = []

    async def handler(_request: FakeRequest) -> FakeResponse:
        return FakeResponse(204)

    response = asyncio.run(
        build_free_middleware(service, event_sender=lambda *args: events.append(args))(
            FakeRequest("/free", {"unload_models": True}),
            handler,
        )
    )

    assert response.status == 204
    assert "X-ComfyUI-LlamaCpp-Release" not in response.headers
    assert events[0][0] == LIFECYCLE_EVENT
    assert events[0][1]["status"] == "failed"
    assert secret not in str(events)


def test_release_event_drops_arbitrary_router_model_diagnostics() -> None:
    secret = "RAW_ROUTER_MODEL_RESPONSE_SECRET"
    service = FakeService()
    service.released_model_diagnostics = (
        {
            "id": "model-a",
            "state": "failed",
            "raw": {"response_body": secret},
            "arbitrary": secret,
        },
    )
    events: list[tuple[str, dict[str, object]]] = []

    async def handler(_request: FakeRequest) -> FakeResponse:
        return FakeResponse()

    asyncio.run(
        build_free_middleware(service, event_sender=lambda *args: events.append(args))(
            FakeRequest("/free", {"free_memory": True}),
            handler,
        )
    )

    assert secret not in str(events)
    assert events[0][1]["released_model_diagnostics"] == [
        {"id": "model-a", "state": "failed", "raw": {}}
    ]


@dataclass
class FakeRoute:
    method: str
    path: str
    handler: object


class FakeRoutes:
    def __init__(self) -> None:
        self.entries: list[FakeRoute] = []

    def __iter__(self):
        return iter(self.entries)

    def _register(self, method: str, path: str):
        def decorator(handler):
            self.entries.append(FakeRoute(method, path, handler))
            return handler

        return decorator

    def get(self, path: str):
        return self._register("GET", path)

    def post(self, path: str):
        return self._register("POST", path)


class FakeMiddlewares(list):
    frozen = False


class FakeWeb:
    @staticmethod
    def middleware(function):
        return function

    @staticmethod
    def json_response(payload, *, status: int = 200):
        response = FakeResponse(status)
        response.payload = payload
        return response


class FakePromptServer:
    def __init__(self) -> None:
        self.app = type("App", (), {"middlewares": FakeMiddlewares()})()
        self.routes = FakeRoutes()
        self.events: list[tuple[str, dict[str, object]]] = []

    def send_sync(self, name: str, payload: dict[str, object], client_id=None) -> None:
        del client_id
        self.events.append((name, payload))


def test_install_is_idempotent_and_registers_only_namespaced_routes_once() -> None:
    prompt_server = FakePromptServer()
    service = FakeService()

    first = install_comfy_bridge(
        service,  # type: ignore[arg-type]
        prompt_server=prompt_server,
        web_module=FakeWeb,
    )
    second = install_comfy_bridge(
        service,  # type: ignore[arg-type]
        prompt_server=prompt_server,
        web_module=FakeWeb,
    )

    assert first.installed is True
    assert first.routes_installed is True
    assert second.installed is True
    assert second.routes_installed is True
    assert len(prompt_server.app.middlewares) == 1
    assert [(route.method, route.path) for route in prompt_server.routes] == [
        ("GET", STATUS_ROUTE),
        ("POST", RELEASE_ROUTE),
        ("GET", PROFILES_ROUTE),
        ("GET", DISCOVERY_ROUTE),
        ("GET", ACTIVE_GENERATIONS_ROUTE),
        ("POST", CANCEL_GENERATION_ROUTE),
    ]


def test_namespaced_status_and_release_routes_are_callable() -> None:
    prompt_server = FakePromptServer()
    service = FakeService()
    service.diagnostic_payload = {"mode": "direct", "owned": True}
    install_comfy_bridge(
        service,  # type: ignore[arg-type]
        prompt_server=prompt_server,
        web_module=FakeWeb,
    )
    routes = {route.path: route.handler for route in prompt_server.routes}

    status_response = asyncio.run(routes[STATUS_ROUTE](FakeRequest(STATUS_ROUTE, {})))
    release_response = asyncio.run(routes[RELEASE_ROUTE](FakeRequest(RELEASE_ROUTE, {})))

    assert status_response.status == 200
    assert status_response.payload == {"mode": "direct", "owned": True}
    assert release_response.status == 200
    assert release_response.payload["status"] == "complete"
    assert service.calls[0][:2] == ("comfy_explicit_api", True)


def test_status_route_withholds_nested_raw_backend_errors() -> None:
    secret = "backend accidentally returned secret material"
    prompt_server = FakePromptServer()
    service = FakeService()
    service.diagnostic_payload = {
        "last_error": secret,
        "last_release": {
            "error": secret,
            "released_model_diagnostics": [
                {
                    "id": "model-a",
                    "state": "failed",
                    "raw": {"response_body": secret},
                    "arbitrary": secret,
                }
            ],
        },
        "process": {"last_error": None},
    }
    install_comfy_bridge(
        service,  # type: ignore[arg-type]
        prompt_server=prompt_server,
        web_module=FakeWeb,
    )
    routes = {route.path: route.handler for route in prompt_server.routes}

    response = asyncio.run(routes[STATUS_ROUTE](FakeRequest(STATUS_ROUTE, {})))

    assert response.status == 200
    assert secret not in str(response.payload)
    assert response.payload["last_error"].startswith("details withheld")
    assert response.payload["last_release"]["error"].startswith("details withheld")
    assert response.payload["last_release"]["released_model_diagnostics"] == [
        {"id": "model-a", "state": "failed", "raw": {}}
    ]


def test_bridge_installation_failure_is_import_safe_and_fail_open() -> None:
    service = FakeService()
    unavailable = install_comfy_bridge(
        service,  # type: ignore[arg-type]
        prompt_server=object(),
        web_module=FakeWeb,
    )
    assert unavailable.installed is False
    assert unavailable.reason == "PromptServer application is unavailable"

    prompt_server = FakePromptServer()
    prompt_server.app.middlewares.frozen = True
    frozen = install_comfy_bridge(
        service,  # type: ignore[arg-type]
        prompt_server=prompt_server,
        web_module=FakeWeb,
    )
    assert frozen.installed is False
    assert frozen.reason == "PromptServer middleware list is frozen"
