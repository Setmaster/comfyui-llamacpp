from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from generation.profiles import TaskProfileSnapshot
from runtime.client import LlamaClientError
from runtime.comfy_routes import (
    ACTIVE_GENERATIONS_ROUTE,
    CANCEL_GENERATION_ROUTE,
    DISCOVERY_ROUTE,
    PROFILES_ROUTE,
    install_generation_routes,
)
from runtime.live_generation import CancelScope, ExecutionIdentity, LiveGenerationRegistry


@dataclass
class FakeRoute:
    method: str
    path: str
    handler: object


class FakeRoutes:
    def __init__(self):
        self.entries = []

    def __iter__(self):
        return iter(self.entries)

    def _register(self, method, path):
        def decorator(handler):
            self.entries.append(FakeRoute(method, path, handler))
            return handler

        return decorator

    def get(self, path):
        return self._register("GET", path)

    def post(self, path):
        return self._register("POST", path)


class FakeResponse:
    def __init__(self, payload, status=200, headers=None):
        self.payload = payload
        self.status = status
        self.headers = headers or {}


class FakeWeb:
    @staticmethod
    def json_response(payload, *, status=200, headers=None):
        return FakeResponse(payload, status, headers)


class FakeRequest:
    def __init__(self, *, query=None, body=None, content_length=None, content=None):
        self.query = query or {}
        self._body = body
        self.content_length = content_length
        self.content = content

    async def json(self):
        return self._body


class FakeContent:
    def __init__(self, value):
        self.value = value
        self.offset = 0

    async def read(self, maximum):
        chunk = self.value[self.offset : self.offset + maximum]
        self.offset += len(chunk)
        return chunk


class FakeUserManager:
    def __init__(self, path):
        self.path = path
        self.calls = []

    def get_request_user_filepath(self, request, path, *, create_dir):
        self.calls.append((request, path, create_dir))
        return self.path


class FakePromptServer:
    def __init__(self, profile_path):
        self.routes = FakeRoutes()
        self.user_manager = FakeUserManager(profile_path)
        self.events = []
        self.sockets = {"client-a": object(), "client-b": object()}

    def send_sync(self, name, payload, client_id):
        self.events.append((name, payload, client_id))


class FakeDiscovery:
    def __init__(self, saved_model):
        self.saved_model = saved_model

    def as_dict(self):
        return {"schema_version": 1, "saved_model": self.saved_model, "models": []}

    def public_dict(self):
        return self.as_dict()


class FakeManager:
    def __init__(self):
        self.calls = []

    def discover_models(self, saved_model):
        self.calls.append(saved_model)
        return FakeDiscovery(saved_model)


def handlers(server):
    return {route.path: route.handler for route in server.routes}


def install(tmp_path):
    server = FakePromptServer(tmp_path / "profiles.json")
    manager = FakeManager()
    registry = LiveGenerationRegistry()
    assert install_generation_routes(
        server,
        FakeWeb,
        manager=manager,  # type: ignore[arg-type]
        registry=registry,
    )
    return server, manager, registry


def test_routes_install_idempotently_and_profiles_are_current_user_bounded(tmp_path):
    server, manager, registry = install(tmp_path)
    assert install_generation_routes(
        server,
        FakeWeb,
        manager=manager,  # type: ignore[arg-type]
        registry=registry,
    )
    assert [(route.method, route.path) for route in server.routes] == [
        ("GET", PROFILES_ROUTE),
        ("GET", DISCOVERY_ROUTE),
        ("GET", ACTIVE_GENERATIONS_ROUTE),
        ("POST", CANCEL_GENERATION_ROUTE),
    ]

    profile = TaskProfileSnapshot("caption", "Caption", prompt_prefix="Describe: ")
    server.user_manager.path.write_text(
        json.dumps({"schema_version": 1, "profiles": [profile.as_dict()]}),
        encoding="utf-8",
    )
    request = FakeRequest()
    response = asyncio.run(handlers(server)[PROFILES_ROUTE](request))

    assert response.status == 200
    assert [item["id"] for item in response.payload["profiles"]] == [
        "freeform",
        "caption",
    ]
    assert server.user_manager.calls == [(request, "comfyui-llamacpp/profiles.json", False)]


def test_profiles_route_normalizes_known_field_surrogates_to_a_bounded_error(tmp_path):
    server, _, _ = install(tmp_path)
    profile = TaskProfileSnapshot("caption", "Caption").as_dict()
    profile["system_prompt"] = "\ud800"
    server.user_manager.path.write_text(
        json.dumps({"schema_version": 1, "profiles": [profile]}),
        encoding="utf-8",
    )

    response = asyncio.run(handlers(server)[PROFILES_ROUTE](FakeRequest()))

    assert response.status == 400
    assert response.payload["error"]["type"] == "ProfileValidationError"
    assert "valid Unicode" in response.payload["error"]["message"]


def test_discovery_route_accepts_only_a_bounded_saved_model_hint(tmp_path):
    server, manager, _ = install(tmp_path)
    route = handlers(server)[DISCOVERY_ROUTE]

    response = asyncio.run(
        route(FakeRequest(query={"saved_model": "exact.gguf", "server_url": "http://evil"}))
    )
    rejected = asyncio.run(route(FakeRequest(query={"saved_model": "x" * 4097})))
    invalid_unicode = asyncio.run(route(FakeRequest(query={"saved_model": "\ud800"})))

    assert response.status == 200
    assert response.payload["saved_model"] == "exact.gguf"
    assert response.headers["Cache-Control"] == "no-store"
    assert manager.calls == ["exact.gguf"]
    assert rejected.status == 400
    assert invalid_unicode.status == 400


def test_discovery_route_never_echoes_arbitrary_upstream_error_text(tmp_path):
    server, _, registry = install(tmp_path)

    class FailingManager:
        def discover_models(self, _saved_model):
            raise LlamaClientError(
                "HTTP 500: RAW_SERVER_BODY_SECRET",
                status_code=500,
                body="RAW_SERVER_BODY_SECRET",
            )

    other_server = FakePromptServer(tmp_path / "other-profiles.json")
    assert install_generation_routes(
        other_server,
        FakeWeb,
        manager=FailingManager(),  # type: ignore[arg-type]
        registry=registry,
    )

    response = asyncio.run(handlers(other_server)[DISCOVERY_ROUTE](FakeRequest()))

    assert response.status == 502
    assert "RAW_SERVER_BODY_SECRET" not in json.dumps(response.payload)
    assert response.payload["error"]["message"] == (
        "Passive runtime discovery failed; inspect Comfy diagnostics."
    )


def test_active_restore_is_targeted_to_one_client_and_bounded(tmp_path):
    server, _, registry = install(tmp_path)
    first = ExecutionIdentity.create(
        prompt_id="prompt-a",
        node_id="7",
        client_id="client-a",
    )
    second = ExecutionIdentity.create(
        prompt_id="prompt-b",
        node_id="8",
        client_id="client-b",
    )
    registry.begin(first)
    registry.begin(second)

    response = asyncio.run(
        handlers(server)[ACTIVE_GENERATIONS_ROUTE](FakeRequest(query={"client_id": "client-a"}))
    )

    assert response.status == 200
    assert [item["execution_id"] for item in response.payload["executions"]] == [
        str(first.execution_id)
    ]
    invalid_unicode = asyncio.run(
        handlers(server)[ACTIVE_GENERATIONS_ROUTE](FakeRequest(query={"client_id": "\ud800"}))
    )
    assert invalid_unicode.status == 400


def test_cancel_route_is_exact_and_never_escalates_prompt_scope(tmp_path):
    server, _, registry = install(tmp_path)
    exact_calls = []
    exact = ExecutionIdentity.create(
        prompt_id="prompt-a",
        node_id="7",
        client_id="client-a",
    )
    prompt_only = ExecutionIdentity.create(
        prompt_id="prompt-b",
        node_id="8",
        client_id="client-a",
    )
    registry.begin(
        exact,
        cancel_scope=CancelScope.GENERATION,
        cancel_callback=lambda: exact_calls.append("delete") or True,
    )
    prompt_handle = registry.begin(prompt_only, cancel_scope=CancelScope.PROMPT)
    route = handlers(server)[CANCEL_GENERATION_ROUTE]

    exact_response = asyncio.run(
        route(
            FakeRequest(
                query={"client_id": "client-a"},
                body={
                    "execution_id": str(exact.execution_id),
                    "prompt_id": "prompt-a",
                    "node_id": "7",
                },
            )
        )
    )
    prompt_response = asyncio.run(
        route(
            FakeRequest(
                query={"client_id": "client-a"},
                body={
                    "execution_id": str(prompt_only.execution_id),
                    "prompt_id": "prompt-b",
                    "node_id": "8",
                },
            )
        )
    )
    injection = asyncio.run(
        route(
            FakeRequest(
                query={"client_id": "client-a"},
                body={
                    "execution_id": str(prompt_only.execution_id),
                    "prompt_id": "prompt-b",
                    "node_id": "8",
                    "server_url": "http://127.0.0.1:1",
                },
            )
        )
    )
    oversized = asyncio.run(
        route(
            FakeRequest(
                query={"client_id": "client-a"},
                body={
                    "execution_id": str(prompt_only.execution_id),
                    "prompt_id": "p" * 513,
                    "node_id": "8",
                },
            )
        )
    )
    oversized_body = asyncio.run(
        route(
            FakeRequest(
                query={"client_id": "client-a"},
                body={},
                content_length=4097,
            )
        )
    )
    chunked_body = asyncio.run(
        route(
            FakeRequest(
                query={"client_id": "client-a"},
                content=FakeContent(b" " * 4097),
            )
        )
    )
    invalid_unicode = asyncio.run(
        route(
            FakeRequest(
                query={"client_id": "client-a"},
                body={
                    "execution_id": str(prompt_only.execution_id),
                    "prompt_id": "\ud800",
                    "node_id": "8",
                },
            )
        )
    )
    invalid_client = asyncio.run(
        route(
            FakeRequest(
                query={"client_id": "\ud800"},
                body={
                    "execution_id": str(prompt_only.execution_id),
                    "prompt_id": "prompt-b",
                    "node_id": "8",
                },
            )
        )
    )

    assert exact_response.status == 202
    assert exact_response.payload["upstream_confirmed"] is True
    assert exact_calls == ["delete"]
    assert prompt_response.status == 409
    assert prompt_response.payload["status"] == "whole_job_required"
    assert prompt_response.payload["scope"] == "prompt"
    assert prompt_handle.token.cancelled is False
    assert injection.status == 400
    assert oversized.status == 400
    assert oversized_body.status == 413
    assert chunked_body.status == 413
    assert invalid_unicode.status == 400
    assert invalid_client.status == 403


def test_cancel_route_accepts_bounded_chunked_utf8_json(tmp_path):
    server, _, registry = install(tmp_path)
    identity = ExecutionIdentity.create(
        prompt_id="prompt-a",
        node_id="7",
        client_id="client-a",
    )
    registry.begin(
        identity,
        cancel_scope=CancelScope.GENERATION,
        cancel_callback=lambda: True,
    )
    body = json.dumps(
        {
            "execution_id": str(identity.execution_id),
            "prompt_id": "prompt-a",
            "node_id": "7",
        }
    ).encode("utf-8")

    response = asyncio.run(
        handlers(server)[CANCEL_GENERATION_ROUTE](
            FakeRequest(
                query={"client_id": "client-a"},
                content=FakeContent(body),
            )
        )
    )

    assert response.status == 202


def test_cancel_and_restore_reject_a_disconnected_or_wrong_client(tmp_path):
    server, _, registry = install(tmp_path)
    identity = ExecutionIdentity.create(
        prompt_id="prompt-a",
        node_id="7",
        client_id="client-a",
    )
    registry.begin(
        identity,
        cancel_scope=CancelScope.GENERATION,
        cancel_callback=lambda: True,
    )
    route = handlers(server)[CANCEL_GENERATION_ROUTE]
    body = {
        "execution_id": str(identity.execution_id),
        "prompt_id": "prompt-a",
        "node_id": "7",
    }

    wrong = asyncio.run(route(FakeRequest(query={"client_id": "client-b"}, body=body)))
    disconnected = asyncio.run(route(FakeRequest(query={"client_id": "not-connected"}, body=body)))
    restore = asyncio.run(
        handlers(server)[ACTIVE_GENERATIONS_ROUTE](
            FakeRequest(query={"client_id": "not-connected"})
        )
    )

    assert wrong.status == 404
    assert disconnected.status == 403
    assert restore.status == 403
