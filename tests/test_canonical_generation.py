from __future__ import annotations

import importlib
import math
import traceback
import uuid
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

import generation.execution as execution_module
from generation.contracts import (
    MAX_TOKEN_BIAS_COUNT,
    MAX_TOKEN_BIAS_JSON_BYTES,
    ErrorCategory,
    GenerationResult,
    GenerationState,
)
from generation.execution import (
    RUNNING_MODEL,
    CanonicalGenerationError,
    CanonicalGenerationExecutor,
)
from generation.profiles import TaskProfileSnapshot
from runtime.client import (
    AuthConfig,
    ConnectionConfig,
    LlamaClientError,
    ResponseProtocolError,
    StreamControl,
    StreamControlProbe,
    StreamControlSupport,
    TLSConfig,
)
from runtime.live_generation import (
    CancelScope,
    ExecutionIdentity,
    LiveGenerationRegistry,
)
from runtime.service import (
    ReleaseResult,
    ReleaseScope,
    ReleaseStatus,
    RuntimeMode,
)
from runtime.streaming import PromptProgress, StreamResult, StreamUpdate


class IncrementingClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        self.value += 0.05
        return self.value


@dataclass
class FakeLease:
    model_id: str | None
    release_after: bool
    mode: RuntimeMode = RuntimeMode.DIRECT


class FakeReleaseHandle:
    def __init__(self, result: ReleaseResult | None = None, error: Exception | None = None):
        self.result = result
        self.error = error
        self.wait_calls: list[float | None] = []
        self.wait_cancels: list[object] = []

    def wait(self, *, timeout=None, cancel=None):
        self.wait_calls.append(timeout)
        self.wait_cancels.append(cancel)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


class FakeRuntimeService:
    def __init__(
        self,
        release_handle: FakeReleaseHandle | None = None,
        *,
        mode: RuntimeMode = RuntimeMode.DIRECT,
    ) -> None:
        self.release_handle = release_handle
        self.mode = mode
        self.begin_calls: list[dict] = []
        self.finish_calls: list[FakeLease] = []
        self.atomic_calls: list[dict] = []

    def begin_generation(self, *, model_id=None, release_after=False, source="generation"):
        self.begin_calls.append(
            {
                "model_id": model_id,
                "release_after": release_after,
                "source": source,
            }
        )
        return FakeLease(model_id, release_after, self.mode)

    def resolve_and_begin_generation(
        self,
        *,
        requested_model,
        resolve_router_model,
        release_after=False,
        source="generation",
    ):
        self.atomic_calls.append(
            {
                "requested_model": requested_model,
                "release_after": release_after,
                "source": source,
            }
        )
        if self.mode == RuntimeMode.ROUTER and not requested_model:
            raise ValueError("router generation requires an exact model ID")
        exact_model = (
            resolve_router_model(requested_model)
            if self.mode == RuntimeMode.ROUTER
            else requested_model
        )
        lease = self.begin_generation(
            model_id=exact_model if self.mode == RuntimeMode.ROUTER else None,
            release_after=release_after,
            source=source,
        )
        return exact_model, lease

    def finish_generation(self, lease):
        self.finish_calls.append(lease)
        return self.release_handle if lease.release_after else None


class FakeManager:
    def __init__(
        self,
        *,
        managed: bool = True,
        router: bool = False,
        canonical_model: str = "canonical/model.gguf",
        service: FakeRuntimeService | None = None,
        projector_status: dict | None = None,
    ) -> None:
        self.managed = managed
        self.is_router_mode = router
        self.canonical_model = canonical_model
        self.projector_status = projector_status
        self.runtime_service = service or FakeRuntimeService(
            mode=RuntimeMode.ROUTER if router else RuntimeMode.DIRECT
        )
        self.runtime_service.mode = RuntimeMode.ROUTER if router else RuntimeMode.DIRECT
        self.connection_calls: list[dict] = []
        self.resolve_calls: list[str] = []
        self.resolve_timeouts: list[float | None] = []

    def connection_for(
        self,
        server_url,
        *,
        api_key_env,
        verify_tls,
        request_timeout,
    ):
        self.connection_calls.append(
            {
                "server_url": server_url,
                "api_key_env": api_key_env,
                "verify_tls": verify_tls,
                "request_timeout": request_timeout,
            }
        )
        endpoint = server_url or "http://127.0.0.1:8080"
        return (
            ConnectionConfig(base_url=endpoint, default_deadline=request_timeout),
            self.managed,
        )

    def resolve_model_id(self, model, timeout=None):
        self.resolve_calls.append(model)
        self.resolve_timeouts.append(timeout)
        return self.canonical_model


class FakeClient:
    def __init__(
        self,
        result: StreamResult | None = None,
        *,
        error: Exception | None = None,
        probe: StreamControlProbe | None = None,
        stream_control=None,
        props=None,
        props_error: Exception | None = None,
    ) -> None:
        self.result = result or successful_stream()
        self.error = error
        self.probe = probe or StreamControlProbe(StreamControlSupport.UNSUPPORTED, "http_404")
        self.prepared_control = stream_control
        self.props = props if props is not None else SimpleNamespace(modalities={})
        self.props_error = props_error
        self.calls: list[dict] = []
        self.prepare_calls: list[dict] = []
        self.props_calls: list[dict] = []
        self.call_order: list[str] = []
        self.closed = False

    def passive_props(self, model=None, **kwargs):
        self.call_order.append("passive_props")
        self.props_calls.append({"model": model, **kwargs})
        if self.props_error is not None:
            raise self.props_error
        return self.props

    def prepare_stream_control(self, **kwargs):
        self.call_order.append("prepare_stream_control")
        self.prepare_calls.append(kwargs)
        return self.probe, self.prepared_control

    def stream_chat(self, payload, **kwargs):
        self.call_order.append("stream_chat")
        self.calls.append({"payload": payload, **kwargs})
        if self.error is not None:
            raise self.error
        kwargs["on_update"](
            StreamUpdate(
                content=self.result.response,
                thinking=self.result.thinking,
                finish_reason=self.result.finish_reason,
                model=self.result.model,
                chunk_index=max(1, self.result.chunks),
            )
        )
        return self.result

    def close(self):
        self.closed = True


class ClientFactory:
    def __init__(self, client: FakeClient) -> None:
        self.client = client
        self.connections: list[ConnectionConfig] = []

    def __call__(self, connection):
        self.connections.append(connection)
        return self.client


def successful_stream(
    response: str = "answer",
    thinking: str = "reasoning",
    *,
    model: str | None = None,
) -> StreamResult:
    return StreamResult(
        response,
        thinking,
        True,
        finish_reason="stop",
        usage={"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
        model=model,
        response_id="chat-local",
        done_received=True,
        chunks=2,
    )


def release_result(
    *,
    status: ReleaseStatus = ReleaseStatus.COMPLETE,
    mode: RuntimeMode = RuntimeMode.DIRECT,
    error: str | None = None,
) -> ReleaseResult:
    return ReleaseResult(
        request_id="request-1",
        source="canonical_generate",
        status=status,
        mode=mode,
        owned=True,
        started_at=1.0,
        completed_at=2.0,
        released_models=("canonical/model.gguf",) if mode == RuntimeMode.ROUTER else (),
        coalesced=True,
        error=error,
        scope=(
            ReleaseScope.ROUTER_MODEL if mode == RuntimeMode.ROUTER else ReleaseScope.DIRECT_RUNTIME
        ),
        operation_id="operation-1",
        target_model="canonical/model.gguf" if mode == RuntimeMode.ROUTER else None,
        superseded_by="global-1",
        driver_memory_verified=False,
    )


def executor(
    *,
    manager: FakeManager | None = None,
    client: FakeClient | None = None,
    live_registry=None,
) -> tuple[CanonicalGenerationExecutor, FakeManager, FakeClient]:
    manager = manager or FakeManager()
    client = client or FakeClient()
    return (
        CanonicalGenerationExecutor(
            manager=manager,
            client_factory=ClientFactory(client),
            live_registry=live_registry,
            clock=IncrementingClock(),
        ),
        manager,
        client,
    )


def test_default_sampling_and_auto_thinking_omit_all_optional_overrides():
    runner, manager, client = executor()
    response, thinking, result = runner.generate("hello")

    assert (response, thinking) == ("answer", "reasoning")
    assert isinstance(result, GenerationResult)
    assert result.state == GenerationState.COMPLETE
    assert result.usage.total_tokens == 6
    payload = client.calls[0]["payload"]
    assert "chat_template_kwargs" not in payload
    assert not {
        "temperature",
        "top_p",
        "top_k",
        "min_p",
        "repeat_penalty",
        "presence_penalty",
        "frequency_penalty",
    } & set(payload)
    assert payload["messages"] == [{"role": "user", "content": "hello"}]
    assert manager.runtime_service.begin_calls == [
        {"model_id": None, "release_after": False, "source": "canonical_generate"}
    ]
    assert len(manager.runtime_service.finish_calls) == 1
    assert client.props_calls == []
    assert client.closed is True


def test_first_chunk_timing_ignores_prompt_progress_updates():
    class ManualClock:
        value = 0.0

        def __call__(self):
            return self.value

    clock = ManualClock()

    class ProgressThenTokenClient(FakeClient):
        def stream_chat(self, payload, **kwargs):
            kwargs["on_update"](
                StreamUpdate(prompt_progress=PromptProgress(10, 0, 5), chunk_index=0)
            )
            clock.value = 3.0
            return super().stream_chat(payload, **kwargs)

    manager = FakeManager()
    client = ProgressThenTokenClient()
    runner = CanonicalGenerationExecutor(
        manager=manager,
        client_factory=ClientFactory(client),
        live_registry=None,
        clock=clock,
    )

    _, _, result = runner.generate("hello")

    assert result.timing.first_chunk_seconds == 3.0


@pytest.mark.parametrize(("thinking", "expected"), (("off", False), ("on", True)))
def test_explicit_thinking_and_custom_sampling_send_the_complete_group(thinking, expected):
    runner, _, client = executor()
    runner.generate(
        "hello",
        thinking_mode=thinking,
        sampling_mode="custom",
        temperature=0.25,
        top_p=0.8,
        top_k=12,
        min_p=0.1,
        repeat_penalty=1.05,
        presence_penalty=-0.2,
        frequency_penalty=0.3,
    )
    payload = client.calls[0]["payload"]
    assert payload["chat_template_kwargs"] == {"enable_thinking": expected}
    assert {
        key: payload[key]
        for key in (
            "temperature",
            "top_p",
            "top_k",
            "min_p",
            "repeat_penalty",
            "presence_penalty",
            "frequency_penalty",
        )
    } == {
        "temperature": 0.25,
        "top_p": 0.8,
        "top_k": 12,
        "min_p": 0.1,
        "repeat_penalty": 1.05,
        "presence_penalty": -0.2,
        "frequency_penalty": 0.3,
    }


def test_connection_and_advanced_url_conflict_before_manager_or_stream():
    runner, manager, client = executor()
    connection = SimpleNamespace(
        server_url="http://localhost:8081",
        model="",
        api_key_env="LLAMACPP_API_KEY",
        verify_tls=True,
        request_timeout=300,
        as_dict=lambda: {
            "schema_version": 1,
            "server_url": "http://localhost:8081",
            "model": "",
            "api_key_env": "LLAMACPP_API_KEY",
            "verify_tls": True,
            "request_timeout": 300,
        },
    )
    with pytest.raises(CanonicalGenerationError) as caught:
        runner.generate("hello", connection=connection, server_url="http://localhost:8082")
    assert caught.value.category == ErrorCategory.INVALID_REQUEST
    assert manager.connection_calls == []
    assert client.calls == []


def test_neither_connection_input_targets_the_managed_runtime():
    runner, manager, _ = executor()
    runner.generate("hello", model=RUNNING_MODEL)
    assert manager.connection_calls[0]["server_url"] == ""


def test_invalid_connection_snapshot_fails_as_request_before_manager_resolution():
    runner, manager, client = executor()
    with pytest.raises(CanonicalGenerationError) as caught:
        runner.generate("hello", server_url="http://user:secret@localhost:8080")
    assert caught.value.category == ErrorCategory.INVALID_REQUEST
    assert manager.connection_calls == []
    assert client.prepare_calls == []


def test_arbitrary_api_key_environment_name_fails_before_manager_or_client():
    runner, manager, client = executor()
    with pytest.raises(CanonicalGenerationError) as caught:
        runner.generate("hello", api_key_env="OPENAI_API_KEY")
    assert caught.value.category == ErrorCategory.INVALID_REQUEST
    assert manager.connection_calls == []
    assert client.prepare_calls == []


@pytest.mark.parametrize(
    ("endpoint", "allowed"),
    (
        ("http://127.0.0.1:8080", True),
        ("http://[::1]:8080", True),
        ("http://localhost:8080", True),
        ("http://worker.localhost:8080", True),
        ("http://192.168.1.20:8080", False),
        ("http://worker.lan:8080", False),
    ),
)
def test_authenticated_plain_http_is_limited_to_loopback(endpoint, allowed):
    class AuthenticatedManager(FakeManager):
        def connection_for(self, server_url, **kwargs):
            self.connection_calls.append({"server_url": server_url, **kwargs})
            return (
                ConnectionConfig(
                    base_url=server_url,
                    auth=AuthConfig("resolved-local-secret"),
                    default_deadline=kwargs["request_timeout"],
                ),
                False,
            )

    manager = AuthenticatedManager(managed=False)
    client = FakeClient()
    runner, _, _ = executor(manager=manager, client=client)
    if allowed:
        runner.generate("hello", server_url=endpoint)
        assert len(client.calls) == 1
        return
    with pytest.raises(CanonicalGenerationError) as caught:
        runner.generate("hello", server_url=endpoint)
    assert caught.value.category == ErrorCategory.INVALID_REQUEST
    assert "requires HTTPS outside loopback" in str(caught.value)
    assert "resolved-local-secret" not in str(caught.value)
    assert client.prepare_calls == []


def test_authenticated_remote_https_requires_exact_server_side_binding_and_tls(
    monkeypatch,
):
    class AuthenticatedManager(FakeManager):
        def connection_for(self, server_url, **kwargs):
            self.connection_calls.append({"server_url": server_url, **kwargs})
            return (
                ConnectionConfig(
                    base_url=server_url,
                    auth=AuthConfig("resolved-local-secret"),
                    tls=TLSConfig(kwargs["verify_tls"]),
                    default_deadline=kwargs["request_timeout"],
                ),
                False,
            )

    endpoint = "https://worker.lan:8443"
    manager = AuthenticatedManager(managed=False)
    client = FakeClient()
    runner, _, _ = executor(manager=manager, client=client)
    with pytest.raises(CanonicalGenerationError, match="origin-to-key binding"):
        runner.generate("hello", server_url=endpoint)
    assert client.prepare_calls == []

    monkeypatch.setenv(
        "LLAMACPP_REMOTE_AUTH_BINDINGS",
        '{"https://worker.lan:8443":"LLAMACPP_API_KEY"}',
    )
    runner.generate("hello", server_url=endpoint)
    assert len(client.calls) == 1

    insecure_client = FakeClient()
    insecure, _, _ = executor(
        manager=AuthenticatedManager(managed=False),
        client=insecure_client,
    )
    with pytest.raises(CanonicalGenerationError, match="certificate verification"):
        insecure.generate("hello", server_url=endpoint, verify_tls=False)
    assert insecure_client.prepare_calls == []


def test_remote_credential_origin_preserves_explicit_port_zero(monkeypatch):
    assert execution_module._credential_origin("https://worker.lan:0") == ("https://worker.lan:0")
    assert execution_module._credential_origin("https://worker.lan") == ("https://worker.lan:443")
    monkeypatch.setenv(
        "LLAMACPP_REMOTE_AUTH_BINDINGS",
        '{"https://worker.lan:443":"LLAMACPP_API_KEY"}',
    )
    with pytest.raises(CanonicalGenerationError, match="origin-to-key binding"):
        execution_module._validate_canonical_transport(
            ConnectionConfig(
                base_url="https://worker.lan:0",
                auth=AuthConfig("resolved-local-secret"),
            ),
            api_key_env="LLAMACPP_API_KEY",
        )


def test_router_resolves_and_leases_exact_model_before_sending_payload():
    manager = FakeManager(router=True, canonical_model="exact/router-id")
    client = FakeClient(successful_stream(model="exact/router-id"))
    runner, _, _ = executor(manager=manager, client=client)
    _, _, result = runner.generate("hello", model="saved/model.gguf")

    assert manager.resolve_calls == ["saved/model.gguf"]
    assert len(manager.resolve_timeouts) == 1
    assert 0 < manager.resolve_timeouts[0] < 300
    assert manager.runtime_service.atomic_calls == [
        {
            "requested_model": "saved/model.gguf",
            "release_after": False,
            "source": "canonical_generate",
        }
    ]
    assert manager.runtime_service.begin_calls[0]["model_id"] == "exact/router-id"
    assert len(manager.connection_calls) == 2
    assert client.calls[0]["payload"]["model"] == "exact/router-id"
    assert result.requested_model == "saved/model.gguf"
    assert result.effective_model == "exact/router-id"


def test_router_missing_model_is_rejected_atomically_without_admitting_a_lease():
    manager = FakeManager(router=True)
    client = FakeClient()
    runner, _, _ = executor(manager=manager, client=client)
    with pytest.raises(CanonicalGenerationError) as caught:
        runner.generate("hello", model=RUNNING_MODEL)
    assert caught.value.category == ErrorCategory.MODEL_MISSING
    assert manager.runtime_service.begin_calls == []
    assert client.calls == []


def test_router_rejects_a_conflicting_response_model_after_finishing_lease():
    manager = FakeManager(router=True, canonical_model="exact/router-id")
    client = FakeClient(successful_stream(model="different-id"))
    runner, _, _ = executor(manager=manager, client=client)
    with pytest.raises(CanonicalGenerationError) as caught:
        runner.generate("hello", model="saved/model.gguf")
    assert caught.value.category == ErrorCategory.PROTOCOL
    assert len(manager.runtime_service.finish_calls) == 1


def test_profile_snapshot_only_transforms_text_and_fills_exactly_empty_system():
    profile = TaskProfileSnapshot(
        "caption",
        "Caption",
        system_prompt="profile system",
        prompt_prefix="prefix<",
        prompt_suffix=">suffix",
    )
    runner, _, client = executor()
    _, _, result = runner.generate(
        "scene",
        profile=profile,
        system_prompt="",
        thinking_mode="off",
        sampling_mode="custom",
        temperature=0.2,
        seed=77,
    )
    payload = client.calls[0]["payload"]
    assert payload["messages"] == [
        {"role": "system", "content": "profile system"},
        {"role": "user", "content": "prefix<scene>suffix"},
    ]
    assert payload["temperature"] == 0.2
    assert payload["seed"] == 77
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}
    assert result.profile_id == "caption"

    runner, _, client = executor()
    runner.generate("scene", profile=profile, system_prompt=" ")
    assert client.calls[0]["payload"]["messages"][0] == {
        "role": "system",
        "content": " ",
    }


def test_images_structured_output_and_token_bans_share_one_strict_path():
    runner, _, client = executor(client=FakeClient(successful_stream('{"ok":true}')))
    _, _, result = runner.generate(
        "inspect",
        images=("data:image/png;base64,one", "data:image/png;base64,two"),
        structured_output={"type": "json_object"},
        token_ban=[["forbidden", False]],
    )
    payload = client.calls[0]["payload"]
    assert payload["messages"][0]["content"] == [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,one"}},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,two"}},
        {"type": "text", "text": "inspect"},
    ]
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["logit_bias"] == [["forbidden", False]]
    assert result.image_count == 2
    assert result.structured_output_kind == "json_object"
    assert "data:image" not in result.to_json()


@pytest.mark.parametrize(
    ("projector_status", "expected_message"),
    (
        (
            {"mode": "auto", "outcome": "text_only"},
            (
                "the selected llama.cpp model reports that vision is unavailable; "
                "use a vision-capable model with its matching projector before connecting "
                "an image"
            ),
        ),
        (
            {"mode": "none", "outcome": "text_only"},
            (
                "image input is connected, but Vision Projector is '(none - text only)'; "
                "select '(auto)' or an exact matching projector, or disconnect the image"
            ),
        ),
    ),
)
def test_known_false_image_capability_fails_before_probe_or_post(
    projector_status,
    expected_message,
):
    manager = FakeManager(projector_status=projector_status)
    client = FakeClient(props=SimpleNamespace(modalities={"vision": False}))
    runner, _, _ = executor(manager=manager, client=client)

    with pytest.raises(CanonicalGenerationError) as caught:
        runner.generate(
            "inspect",
            images=("data:image/png;base64,one",),
        )

    assert caught.value.category == ErrorCategory.CAPABILITY_UNSUPPORTED
    assert caught.value.info.message == expected_message
    assert client.call_order == ["passive_props"]
    assert client.props_calls[0]["model"] is None
    assert 0 < client.props_calls[0]["timeout"] <= 2.0
    assert client.prepare_calls == []
    assert client.calls == []
    assert len(manager.runtime_service.begin_calls) == 1
    assert len(manager.runtime_service.finish_calls) == 1
    assert client.closed is True


def test_unknown_image_capability_continues_before_probe_and_post():
    client = FakeClient(props=SimpleNamespace(modalities={}))
    runner, _, _ = executor(client=client)

    response, _, _ = runner.generate(
        "inspect",
        images=("data:image/png;base64,one",),
    )

    assert response == "answer"
    assert client.call_order == [
        "passive_props",
        "prepare_stream_control",
        "stream_chat",
    ]


def test_unavailable_attached_props_preserve_generation_compatibility():
    manager = FakeManager(managed=False)
    client = FakeClient(
        successful_stream(model="attached/model"),
        props_error=LlamaClientError("/props is unavailable"),
    )
    runner, _, _ = executor(manager=manager, client=client)

    response, _, _ = runner.generate(
        "inspect",
        server_url="http://127.0.0.1:8081",
        model="attached/model",
        images=("data:image/png;base64,one",),
    )

    assert response == "answer"
    assert client.props_calls[0]["model"] == "attached/model"
    assert client.call_order == [
        "passive_props",
        "prepare_stream_control",
        "stream_chat",
    ]
    assert manager.runtime_service.begin_calls == []


def test_malformed_props_preserve_image_generation_compatibility():
    client = FakeClient(
        successful_stream(),
        props_error=ResponseProtocolError(
            "response body was not valid JSON",
            endpoint="/props",
        ),
    )
    runner, _, _ = executor(client=client)

    response, _, _ = runner.generate(
        "inspect",
        images=("data:image/png;base64,one",),
    )

    assert response == "answer"
    assert client.call_order == [
        "passive_props",
        "prepare_stream_control",
        "stream_chat",
    ]


def test_router_image_probe_uses_the_exact_admitted_model_without_autoloading():
    manager = FakeManager(router=True, canonical_model="exact/router-id")
    client = FakeClient(
        successful_stream(model="exact/router-id"),
        props=SimpleNamespace(modalities={}),
    )
    runner, _, _ = executor(manager=manager, client=client)

    runner.generate(
        "inspect",
        model="saved/model.gguf",
        images=("data:image/png;base64,one",),
    )

    assert client.props_calls[0]["model"] == "exact/router-id"
    assert 0 < client.props_calls[0]["timeout"] <= 2.0
    assert client.call_order[:2] == ["passive_props", "prepare_stream_control"]


def test_image_unsupported_stream_token_maps_to_fixed_non_leaking_error():
    raw_sentinel = "RAW_UPSTREAM_IMAGE_ERROR_BODY"
    stream = StreamResult(
        "",
        "",
        False,
        error_message=raw_sentinel,
        error_type="image_unsupported",
        status_code=400,
    )
    client = FakeClient(
        stream,
        props_error=LlamaClientError("/props unavailable"),
    )
    runner, _, _ = executor(client=client)

    with pytest.raises(CanonicalGenerationError) as caught:
        runner.generate(
            "inspect",
            images=("data:image/png;base64,one",),
        )

    assert caught.value.category == ErrorCategory.CAPABILITY_UNSUPPORTED
    assert caught.value.info.message == (
        "the selected llama.cpp model reports that vision is unavailable; "
        "use a vision-capable model with its matching projector before connecting an image"
    )
    assert raw_sentinel not in str(caught.value)
    assert client.call_order == [
        "passive_props",
        "prepare_stream_control",
        "stream_chat",
    ]


@pytest.mark.parametrize(
    "token_ban",
    (
        {"not": "a sequence"},
        [["token"]],
        [["token", "false"]],
        [[object(), False]],
        [["\ud800", False]],
    ),
)
def test_invalid_token_bias_fails_before_connection_or_lease_admission(token_ban):
    runner, manager, client = executor()

    with pytest.raises(CanonicalGenerationError) as caught:
        runner.generate("hello", token_ban=token_ban)

    assert caught.value.category == ErrorCategory.INVALID_REQUEST
    assert manager.connection_calls == []
    assert manager.runtime_service.begin_calls == []
    assert client.prepare_calls == []


@pytest.mark.parametrize(
    "token_ban",
    (
        [["x", False]] * (MAX_TOKEN_BIAS_COUNT + 1),
        [["x" * 4096, False]] * (MAX_TOKEN_BIAS_JSON_BYTES // 4096 + 1),
    ),
)
def test_token_bias_resource_limits_fail_before_connection_or_lease_admission(token_ban):
    runner, manager, client = executor()

    with pytest.raises(CanonicalGenerationError) as caught:
        runner.generate("hello", token_ban=token_ban)

    assert caught.value.category == ErrorCategory.INVALID_REQUEST
    assert manager.connection_calls == []
    assert manager.runtime_service.begin_calls == []
    assert client.prepare_calls == []


def test_default_sampling_ignores_irrelevant_invalid_expert_values():
    runner, _, client = executor()
    runner.generate(
        "hello",
        sampling_mode="default",
        temperature=float("nan"),
        top_p=object(),
        top_k=True,
        min_p=float("inf"),
        repeat_penalty=-100,
        presence_penalty=object(),
        frequency_penalty=object(),
    )
    assert (
        not {
            "temperature",
            "top_p",
            "top_k",
            "min_p",
            "repeat_penalty",
            "presence_penalty",
            "frequency_penalty",
        }
        & client.calls[0]["payload"].keys()
    )

    invalid_custom, manager, client = executor()
    with pytest.raises(CanonicalGenerationError) as caught:
        invalid_custom.generate("hello", sampling_mode="custom", temperature=float("nan"))
    assert caught.value.category == ErrorCategory.INVALID_REQUEST
    assert manager.connection_calls == []
    assert client.prepare_calls == []


def test_invalid_structured_json_is_a_categorized_total_failure():
    runner, _, _ = executor(client=FakeClient(successful_stream("not json")))
    with pytest.raises(CanonicalGenerationError) as caught:
        runner.generate("hello", structured_output={"type": "json_object"})
    assert caught.value.category == ErrorCategory.STRUCTURED_JSON


@pytest.mark.parametrize("response", ("[]", "NaN", "Infinity", "-Infinity"))
def test_json_object_rejects_non_objects_and_nonstandard_constants(response):
    runner, _, _ = executor(client=FakeClient(successful_stream(response)))

    with pytest.raises(CanonicalGenerationError) as caught:
        runner.generate("hello", structured_output={"type": "json_object"})

    assert caught.value.category == ErrorCategory.STRUCTURED_JSON


def test_json_schema_accepts_a_standard_array_but_rejects_nonstandard_constants():
    schema = {
        "type": "json_schema",
        "json_schema": {
            "name": "array_result",
            "strict": True,
            "schema": {"type": "array", "items": {"type": "string"}},
        },
    }
    runner, _, _ = executor(client=FakeClient(successful_stream('["valid"]')))
    assert runner.generate("hello", structured_output=schema)[0] == '["valid"]'

    runner, _, _ = executor(client=FakeClient(successful_stream("NaN")))
    with pytest.raises(CanonicalGenerationError) as caught:
        runner.generate("hello", structured_output=schema)
    assert caught.value.category == ErrorCategory.STRUCTURED_JSON


def test_canonical_structured_request_rejects_nonstandard_json_values_before_client_use():
    runner, manager, client = executor()
    schema = {
        "type": "json_schema",
        "json_schema": {
            "name": "invalid",
            "strict": True,
            "schema": {"type": "number", "minimum": float("nan")},
        },
    }

    with pytest.raises(CanonicalGenerationError) as caught:
        runner.generate("hello", structured_output=schema)

    assert caught.value.category == ErrorCategory.INVALID_REQUEST
    assert manager.connection_calls == []
    assert client.prepare_calls == []


@pytest.mark.parametrize(
    "partial_policy",
    ["raise_error", "return_marked_partial"],
)
def test_partial_output_is_withheld_by_default_and_typed_when_explicit(partial_policy):
    partial = StreamResult(
        "unfinished",
        "",
        False,
        error_message="stream ended early",
        error_type="incomplete",
        partial=True,
        chunks=1,
    )
    runner, _, _ = executor(client=FakeClient(partial))
    if partial_policy == "raise_error":
        with pytest.raises(CanonicalGenerationError) as caught:
            runner.generate("hello", partial_output_policy=partial_policy)
        assert caught.value.category == ErrorCategory.PROTOCOL
        return

    response, _, result = runner.generate("hello", partial_output_policy=partial_policy)
    assert response == "unfinished"
    assert result.state == GenerationState.PARTIAL
    assert result.partial is True
    assert result.error is not None
    assert result.error.category == ErrorCategory.PROTOCOL


def test_attached_release_is_rejected_before_client_creation_or_prompt_submission():
    manager = FakeManager(managed=False)
    client = FakeClient()
    factory = ClientFactory(client)
    runner = CanonicalGenerationExecutor(
        manager=manager,
        client_factory=factory,
        live_registry=None,
    )
    with pytest.raises(CanonicalGenerationError) as caught:
        runner.generate(
            "hello",
            server_url="http://attached.local:8080",
            release_after_generation=True,
        )
    assert caught.value.category == ErrorCategory.INVALID_REQUEST
    assert factory.connections == []
    assert client.calls == []
    assert manager.runtime_service.begin_calls == []


def test_attached_connection_model_passes_through_without_router_identity_inference():
    manager = FakeManager(managed=False, router=False)
    client = FakeClient(successful_stream(model="attached/server-alias"))
    runner, _, _ = executor(manager=manager, client=client)
    connection = SimpleNamespace(
        server_url="http://attached.local:8080",
        model="attached/requested-model",
        api_key_env="LLAMACPP_API_KEY",
        verify_tls=True,
        request_timeout=300,
        as_dict=lambda: {
            "schema_version": 1,
            "server_url": "http://attached.local:8080",
            "model": "attached/requested-model",
            "api_key_env": "LLAMACPP_API_KEY",
            "verify_tls": True,
            "request_timeout": 300,
        },
    )
    _, _, result = runner.generate("hello", connection=connection)
    assert client.calls[0]["payload"]["model"] == "attached/requested-model"
    assert result.requested_model == "attached/requested-model"
    assert result.effective_model == "attached/server-alias"
    assert manager.runtime_service.atomic_calls == []
    assert manager.runtime_service.begin_calls == []


def test_release_after_waits_and_returns_only_normalized_terminal_metadata():
    handle = FakeReleaseHandle(release_result(mode=RuntimeMode.ROUTER))
    service = FakeRuntimeService(handle)
    manager = FakeManager(router=True, service=service)
    client = FakeClient(successful_stream(model="canonical/model.gguf"))
    runner, _, _ = executor(manager=manager, client=client)
    _, _, result = runner.generate(
        "hello",
        model="saved.gguf",
        release_after_generation=True,
        request_timeout=17,
    )

    assert len(handle.wait_calls) == 1
    assert 0 < handle.wait_calls[0] < 17
    assert result.release.as_dict() == {
        "schema_version": 1,
        "policy": "release_after_generation",
        "status": "complete",
        "mode": "router",
        "target_model": "canonical/model.gguf",
        "request_id": "request-1",
        "operation_id": "operation-1",
        "scope": "router_model",
        "coalesced": True,
        "superseded_by": "global-1",
        "driver_memory_verified": False,
        "terminal": True,
        "success": True,
        "elapsed_seconds": pytest.approx(0.1),
        "released_models": ["canonical/model.gguf"],
        "error": None,
    }


def test_release_wait_uses_remaining_deadline_and_only_whole_job_cancel():
    handle = FakeReleaseHandle(release_result())
    manager = FakeManager(service=FakeRuntimeService(handle))
    client = FakeClient()

    def caller_cancel():
        return False

    runner, _, _ = executor(manager=manager, client=client)
    runner.generate(
        "hello",
        release_after_generation=True,
        request_timeout=1,
        cancel_check=caller_cancel,
    )
    assert 0 < client.prepare_calls[0]["probe_timeout"] < 1
    assert client.prepare_calls[0]["delete_timeout"] == 2.0
    assert 0 < client.calls[0]["timeout"] < 1
    assert 0 <= handle.wait_calls[0] < client.calls[0]["timeout"]
    assert handle.wait_cancels == [caller_cancel]


def test_generation_scoped_cancel_token_does_not_abandon_terminal_release_wait():
    registry = LiveGenerationRegistry()
    identity = ExecutionIdentity.create(prompt_id="prompt-1", node_id="12")
    handle = FakeReleaseHandle(release_result())
    manager = FakeManager(service=FakeRuntimeService(handle))

    class Control:
        def delete(self):
            return True

    class CancelledClient(FakeClient):
        def stream_chat(self, payload, **kwargs):
            registry.cancel(identity.execution_id, prompt_id="prompt-1", node_id="12")
            assert kwargs["cancel"]() is True
            return StreamResult(
                "partial",
                "",
                False,
                error_message="generation cancelled",
                error_type="cancelled",
                partial=True,
                cancelled=True,
            )

    client = CancelledClient(
        probe=StreamControlProbe(StreamControlSupport.SUPPORTED),
        stream_control=Control(),
    )
    runner, _, _ = executor(manager=manager, client=client, live_registry=registry)
    _, _, result = runner.generate(
        "hello",
        identity=identity,
        release_after_generation=True,
        partial_output_policy="return_marked_partial",
    )
    assert result.state == GenerationState.CANCELLED
    assert len(handle.wait_calls) == 1
    assert handle.wait_cancels == [None]


def test_release_phase_disables_exact_stop_before_waiting_for_cleanup():
    registry = LiveGenerationRegistry()
    identity = ExecutionIdentity.create(prompt_id="prompt-1", node_id="12")

    class Control:
        def delete(self):  # pragma: no cover - cancellation must be disabled
            raise AssertionError("exact stream DELETE must be unavailable during release")

    class InspectingHandle(FakeReleaseHandle):
        def wait(self, *, timeout=None, cancel=None):
            snapshot = registry.snapshot(identity.execution_id)
            assert snapshot is not None
            assert snapshot.phase == "releasing"
            assert snapshot.cancel_scope == CancelScope.NONE
            assert snapshot.cancel_enabled is False
            return super().wait(timeout=timeout, cancel=cancel)

    handle = InspectingHandle(release_result())
    manager = FakeManager(service=FakeRuntimeService(handle))
    client = FakeClient(
        successful_stream(),
        probe=StreamControlProbe(StreamControlSupport.SUPPORTED),
        stream_control=Control(),
    )
    runner, _, _ = executor(manager=manager, client=client, live_registry=registry)
    runner.generate("hello", identity=identity, release_after_generation=True)
    assert registry.snapshot(identity.execution_id) is None


@pytest.mark.parametrize(
    "handle",
    [
        FakeReleaseHandle(error=TimeoutError("release wait expired")),
        FakeReleaseHandle(release_result(status=ReleaseStatus.DEFERRED)),
        FakeReleaseHandle(
            release_result(status=ReleaseStatus.FAILED, error="router unload failed")
        ),
    ],
)
def test_release_wait_or_terminal_failure_withholds_outputs(handle):
    service = FakeRuntimeService(handle)
    manager = FakeManager(service=service)
    runner, _, _ = executor(manager=manager)
    with pytest.raises(CanonicalGenerationError) as caught:
        runner.generate("hello", release_after_generation=True)
    assert caught.value.category == ErrorCategory.RELEASE_FAILED


def test_generation_failure_keeps_its_category_and_records_secondary_release_failure():
    handle = FakeReleaseHandle(
        release_result(status=ReleaseStatus.FAILED, error="runtime cleanup failed")
    )
    manager = FakeManager(service=FakeRuntimeService(handle))
    failed_stream = StreamResult(
        "",
        "",
        False,
        error_message="authentication denied",
        error_type="http",
        status_code=401,
    )
    runner, _, _ = executor(manager=manager, client=FakeClient(failed_stream))
    with pytest.raises(CanonicalGenerationError) as caught:
        runner.generate("hello", release_after_generation=True)
    assert caught.value.category == ErrorCategory.AUTHENTICATION
    assert "rejected local authentication" in str(caught.value)
    assert "terminal runtime release also failed" in str(caught.value)
    assert "inspect local runtime diagnostics" in str(caught.value)
    assert "runtime cleanup failed" not in str(caught.value)


def test_missing_release_handle_fails_closed_and_withholds_outputs():
    manager = FakeManager(service=FakeRuntimeService(release_handle=None))
    runner, _, _ = executor(manager=manager)
    with pytest.raises(CanonicalGenerationError) as caught:
        runner.generate("hello", release_after_generation=True)
    assert caught.value.category == ErrorCategory.RELEASE_FAILED
    assert "release handle" in str(caught.value)


@pytest.mark.parametrize(
    ("stream", "category"),
    [
        (
            StreamResult("", "", False, error_message="denied", error_type="http", status_code=401),
            ErrorCategory.AUTHENTICATION,
        ),
        (
            StreamResult(
                "",
                "",
                False,
                error_message="TLS certificate verify failed",
                error_type="transport",
            ),
            ErrorCategory.TLS,
        ),
        (
            StreamResult("", "", False, error_message="deadline", error_type="timeout"),
            ErrorCategory.TIMEOUT,
        ),
        (
            StreamResult(
                "",
                "",
                False,
                error_message="HTTP 404: stream request failed",
                error_type="model_missing",
                status_code=404,
            ),
            ErrorCategory.MODEL_MISSING,
        ),
    ],
)
def test_common_failures_have_stable_categories(stream, category):
    runner, _, _ = executor(client=FakeClient(stream))
    with pytest.raises(CanonicalGenerationError) as caught:
        runner.generate("hello")
    assert caught.value.category == category


def test_exception_messages_are_redacted_and_bounded():
    secret = "super-secret-token"
    connection = ConnectionConfig(
        base_url="http://localhost:8080",
        default_headers=(("Authorization", f"Bearer {secret}"),),
    )

    class SecretManager(FakeManager):
        def connection_for(self, *args, **kwargs):
            return connection, True

    client = FakeClient(error=LlamaClientError(f"Authorization: Bearer {secret}"))
    runner, _, _ = executor(manager=SecretManager(), client=client)
    with pytest.raises(CanonicalGenerationError) as caught:
        runner.generate("hello")
    assert secret not in str(caught.value)
    assert "could not reach the local llama-server endpoint" in str(caught.value)
    formatted = "".join(traceback.format_exception(caught.type, caught.value, caught.tb))
    assert secret not in formatted


@pytest.mark.parametrize(
    "stream",
    (
        StreamResult(
            "",
            "",
            False,
            error_message="HTTP 401: RAW_SERVER_BODY_SECRET",
            error_type="http",
            status_code=401,
        ),
        StreamResult(
            "",
            "",
            False,
            error_message="upstream exploded: RAW_SERVER_BODY_SECRET",
            error_type="server",
            status_code=500,
        ),
    ),
)
def test_http_and_server_stream_errors_never_echo_raw_upstream_text(stream):
    runner, _, _ = executor(client=FakeClient(stream))
    with pytest.raises(CanonicalGenerationError) as caught:
        runner.generate("hello")
    assert "RAW_SERVER_BODY_SECRET" not in str(caught.value)


def test_protocol_errors_never_echo_raw_stream_bodies():
    stream = StreamResult(
        "",
        "",
        False,
        error_message="invalid JSON stream chunk: RAW_SERVER_BODY_SECRET",
        error_type="protocol",
    )
    runner, _, _ = executor(client=FakeClient(stream))
    with pytest.raises(CanonicalGenerationError) as caught:
        runner.generate("hello")
    assert caught.value.category == ErrorCategory.PROTOCOL
    assert "RAW_SERVER_BODY_SECRET" not in str(caught.value)
    assert "invalid streamed response" in str(caught.value)


def test_tls_probe_exception_is_classified_before_prompt_submission():
    client = FakeClient()

    def failed_probe(**kwargs):
        assert kwargs["probe_timeout"] <= 2.0
        raise LlamaClientError("SSL certificate verification failed")

    client.prepare_stream_control = failed_probe
    runner, manager, _ = executor(client=client, live_registry=LiveGenerationRegistry())
    identity = ExecutionIdentity.create(prompt_id="prompt-1", node_id="12")
    with pytest.raises(CanonicalGenerationError) as caught:
        runner.generate("hello", identity=identity)
    assert caught.value.category == ErrorCategory.TLS
    assert client.calls == []
    assert len(manager.runtime_service.begin_calls) == 1
    assert len(manager.runtime_service.finish_calls) == 1
    assert client.closed is True


def test_supported_live_stop_is_exact_and_returns_typed_cancelled_partial():
    registry = LiveGenerationRegistry()
    identity = ExecutionIdentity.create(
        prompt_id="prompt-1",
        node_id="dynamic-12",
        display_node_id="12",
        real_node_id="12",
        parent_node_id="12",
        list_index=0,
        client_id="client-a",
    )

    class Control:
        def __init__(self):
            self.delete_calls = 0

        def delete(self):
            self.delete_calls += 1
            return True

    control = Control()

    class CancellingClient(FakeClient):
        def stream_chat(self, payload, **kwargs):
            self.calls.append({"payload": payload, **kwargs})
            snapshot = registry.snapshot(identity.execution_id)
            assert snapshot is not None
            assert snapshot.cancel_scope == CancelScope.GENERATION
            callback = registry._active[identity.execution_id].cancel_callback
            assert callback is not None
            assert "live" not in callback.__code__.co_freevars
            cancelled = registry.cancel(
                identity.execution_id,
                prompt_id="prompt-1",
                node_id="dynamic-12",
            )
            assert cancelled.accepted is True
            assert cancelled.scope == CancelScope.GENERATION
            assert kwargs["cancel"]() is True
            return StreamResult(
                "partial response",
                "",
                False,
                error_message="generation cancelled",
                error_type="cancelled",
                partial=True,
                cancelled=True,
                chunks=1,
            )

    client = CancellingClient(
        probe=StreamControlProbe(StreamControlSupport.SUPPORTED),
        stream_control=control,
    )
    runner, _, _ = executor(client=client, live_registry=registry)
    response, _, result = runner.generate(
        "hello",
        identity=identity,
        partial_output_policy="return_marked_partial",
    )
    assert response == "partial response"
    assert result.state == GenerationState.CANCELLED
    assert result.error is not None
    assert result.error.category == ErrorCategory.CANCELLED
    assert control.delete_calls == 1
    assert client.calls[0]["stream_control"] is control
    assert registry.snapshot(identity.execution_id) is None


def test_headless_generation_still_uses_exact_stream_control_and_cleanup_contract():
    class Control:
        def __init__(self):
            self.delete_calls = 0

        def delete(self):
            self.delete_calls += 1
            return True

    control = Control()

    class CleanupClient(FakeClient):
        def stream_chat(self, payload, **kwargs):
            assert kwargs["stream_control"] is control
            assert control.delete() is True
            result = successful_stream()
            object.__setattr__(
                result,
                "stream_cleanup",
                {
                    "attempts": 1,
                    "confirmed": True,
                    "failures": 0,
                    "last_error_type": None,
                },
            )
            self.calls.append({"payload": payload, **kwargs})
            return result

    client = CleanupClient(
        probe=StreamControlProbe(StreamControlSupport.SUPPORTED),
        stream_control=control,
    )
    runner, _, _ = executor(client=client, live_registry=None)
    runner.generate("hello", identity=None, request_timeout=1)
    assert len(client.prepare_calls) == 1
    assert 0 < client.prepare_calls[0]["probe_timeout"] < 1
    assert client.calls[0]["stream_control"] is control
    assert control.delete_calls == 1


def test_unsupported_live_control_truthfully_warns_and_keeps_prompt_scope():
    registry = LiveGenerationRegistry()
    identity = ExecutionIdentity.create(prompt_id="prompt-1", node_id="12")

    class InspectingClient(FakeClient):
        def stream_chat(self, payload, **kwargs):
            snapshot = registry.snapshot(identity.execution_id)
            assert snapshot is not None
            assert snapshot.cancel_scope == CancelScope.PROMPT
            assert kwargs["stream_control"] is None
            return super().stream_chat(payload, **kwargs)

    client = InspectingClient(
        probe=StreamControlProbe(StreamControlSupport.UNSUPPORTED, "http_404")
    )
    runner, _, _ = executor(client=client, live_registry=registry)
    _, _, result = runner.generate("hello", identity=identity)
    assert result.warnings == ("generation-scoped cancellation unavailable: http_404",)
    assert registry.snapshot(identity.execution_id) is None


def test_missing_prompt_identity_never_exposes_a_global_interrupt_fallback():
    registry = LiveGenerationRegistry()
    identity = ExecutionIdentity.create(prompt_id=None, node_id="12")

    class InspectingClient(FakeClient):
        def stream_chat(self, payload, **kwargs):
            snapshot = registry.snapshot(identity.execution_id)
            assert snapshot is not None
            assert snapshot.cancel_scope == CancelScope.NONE
            assert snapshot.cancel_enabled is False
            return super().stream_chat(payload, **kwargs)

    client = InspectingClient(
        probe=StreamControlProbe(StreamControlSupport.UNSUPPORTED, "http_404")
    )
    runner, _, _ = executor(client=client, live_registry=registry)
    runner.generate("hello", identity=identity)
    assert registry.snapshot(identity.execution_id) is None


@pytest.mark.parametrize(
    ("cleanup", "expected_warning"),
    (
        (
            {
                "attempts": 1,
                "confirmed": False,
                "failures": 0,
                "last_error_type": None,
            },
            "upstream stream cleanup was not confirmed after 1 attempt(s)",
        ),
        (
            {
                "attempts": 2,
                "confirmed": False,
                "failures": 1,
                "last_error_type": "LlamaClientError",
            },
            "upstream stream cleanup was not confirmed after 2 attempt(s) (LlamaClientError)",
        ),
        (
            {
                "attempts": 1,
                "confirmed": True,
                "failures": 0,
                "last_error_type": None,
            },
            None,
        ),
    ),
)
def test_stream_cleanup_confirmation_is_reported_without_inference(
    cleanup,
    expected_warning,
):
    stream = successful_stream()
    object.__setattr__(stream, "stream_cleanup", cleanup)
    runner, _, _ = executor(client=FakeClient(stream))
    _, _, result = runner.generate("hello")
    if expected_warning is None:
        assert not any("stream cleanup" in warning for warning in result.warnings)
    else:
        assert expected_warning in result.warnings


def test_comfy_base_exception_terminal_event_preserves_exact_stream_cleanup_evidence():
    class ComfyInterrupt(BaseException):
        pass

    events = []
    registry = LiveGenerationRegistry(
        sender=lambda name, payload, client_id: events.append((name, payload, client_id))
    )
    identity = ExecutionIdentity.create(
        prompt_id="prompt-1",
        node_id="12",
        client_id="client-a",
    )

    class InterruptingClient(FakeClient):
        def stream_chat(self, payload, **kwargs):
            self.calls.append({"payload": payload, **kwargs})
            assert kwargs["stream_control"] is self.prepared_control
            assert kwargs["stream_control"].delete() is True
            raise ComfyInterrupt()

    control = StreamControl(
        ConnectionConfig(),
        uuid.uuid4(),
        _delete=lambda _identity, _timeout: True,
    )
    client = InterruptingClient(
        probe=StreamControlProbe(StreamControlSupport.SUPPORTED),
        stream_control=control,
    )
    handle = FakeReleaseHandle(release_result())
    manager = FakeManager(service=FakeRuntimeService(handle))
    runner, _, _ = executor(manager=manager, client=client, live_registry=registry)

    with pytest.raises(ComfyInterrupt):
        runner.generate(
            "hello",
            identity=identity,
            release_after_generation=True,
        )

    terminal = events[-1][1]
    assert terminal["terminal"] is True
    assert terminal["phase"] == "cancelled"
    assert terminal["stream_cleanup"] == {
        "attempts": 1,
        "confirmed": True,
        "failures": 0,
        "last_error_type": None,
    }
    assert terminal["release"]["status"] == "accepted_continuing"
    assert terminal["release"]["policy"] == "release_after_generation"
    assert terminal["release"]["terminal"] is False
    assert handle.wait_calls == []


def test_generate_node_contract_is_noncacheable_output_capable_and_app_mode_ready(
    node_package,
    monkeypatch,
):
    module = importlib.import_module(f"{node_package.__name__}.nodes.generate")
    node_class = module.LlamaCppGenerate
    schema = node_class.INPUT_TYPES()

    assert list(schema) == ["required", "optional", "hidden"]
    assert list(schema["required"]) == ["prompt"]
    assert list(schema["optional"]) == [
        "connection",
        "profile",
        "server_url",
        "model",
        "system_prompt",
        "thinking_mode",
        "max_tokens",
        "sampling_mode",
        "temperature",
        "top_p",
        "top_k",
        "min_p",
        "repeat_penalty",
        "presence_penalty",
        "frequency_penalty",
        "seed",
        "cache_prompt",
        "stop_sequences",
        "api_key_env",
        "verify_tls",
        "request_timeout",
        "image_amount",
        "include_image_batch",
        "release_after_generation",
        "partial_output_policy",
        "image_1",
        "image_2",
        "image_3",
        "image_4",
        "image_5",
        "image_6",
        "image_7",
        "image_8",
        "image_9",
        "image_10",
        "structured_output",
        "token_ban",
    ]
    assert schema["hidden"] == {
        "unique_id": "UNIQUE_ID",
        "dynprompt": "DYNPROMPT",
        "extra_pnginfo": "EXTRA_PNGINFO",
    }
    model_choices, model_options = schema["optional"]["model"]
    assert isinstance(model_choices, list)
    assert model_choices[0] == RUNNING_MODEL
    assert model_options["default"] == RUNNING_MODEL
    assert schema["optional"]["seed"][1]["control_after_generate"] is True
    assert node_class.RETURN_TYPES == ("STRING", "STRING", "LLAMACPP_GENERATION_RESULT")
    assert node_class.RETURN_NAMES == ("response", "thinking", "result")
    assert node_class.OUTPUT_NODE is True
    assert math.isnan(node_class.IS_CHANGED())

    typed_result = object()

    class StubExecutor:
        def generate(self, prompt, **kwargs):
            assert prompt == "hello"
            return "final response", "thoughts", typed_result

    monkeypatch.setattr(module, "CanonicalGenerationExecutor", StubExecutor)
    output = node_class().generate("hello")
    assert output == {
        "ui": {"text": ("final response",)},
        "result": ("final response", "thoughts", typed_result),
    }


def test_task_profile_remains_cacheable_by_its_saved_primitive(node_package):
    module = importlib.import_module(f"{node_package.__name__}.nodes.task_profile")
    assert "IS_CHANGED" not in vars(module.LlamaCppTaskProfile)


def test_generate_node_captures_dynamic_identity_without_serializing_live_state(node_package):
    module = importlib.import_module(f"{node_package.__name__}.nodes.generate")

    class DynamicPrompt:
        def get_display_node_id(self, node_id):
            assert node_id == "ephemeral-12"
            return "display-12"

        def get_real_node_id(self, node_id):
            return "real-12"

        def get_parent_node_id(self, node_id):
            return "parent-12"

    identity = module._execution_identity(
        "ephemeral-12",
        DynamicPrompt(),
        {"workflow": {"id": "workflow-a"}},
    )
    assert identity is not None
    assert identity.prompt_id is None
    assert identity.node_id == "ephemeral-12"
    assert identity.display_node_id == "display-12"
    assert identity.real_node_id == "real-12"
    assert identity.parent_node_id == "parent-12"
    assert identity.workflow_id == "workflow-a"
    assert "execution_id" not in module.LlamaCppGenerate.INPUT_TYPES()["optional"]
