"""Budgeting must count Generate's actual request and obey its operation lifecycle."""

from __future__ import annotations

import importlib
import json
from hashlib import sha256
from types import SimpleNamespace

import pytest
from test_canonical_generation import (
    FakeClient,
    FakeManager,
    FakeReleaseHandle,
    FakeRuntimeService,
    executor,
    release_result,
    successful_stream,
)

from generation.budget import RequestBudget
from generation.contracts import ErrorCategory, GenerationResult
from generation.execution import CanonicalGenerationError
from generation.messages import ConversationMessages, TextMessage
from generation.profiles import TaskProfileSnapshot
from runtime.client import (
    DeadlineExceeded,
    InputTokenCount,
    InputTokenSupport,
    LlamaClientError,
    OperationCancelled,
    ResponseProtocolError,
)
from runtime.live_generation import ExecutionIdentity, LiveGenerationRegistry
from runtime.service import ReleaseStatus, RuntimeMode


class BudgetClient(FakeClient):
    def __init__(self, *, count=12, context=100, count_error=None, props_error=None):
        super().__init__(
            successful_stream('{"answer":"ok"}'),
            props=SimpleNamespace(
                modalities={"vision": True},
                raw={"default_generation_settings": {"n_ctx": context}, "n_ctx": 99999},
                model_alias="direct-model",
            ),
            props_error=props_error,
        )
        self.count = count
        self.count_error = count_error
        self.count_calls = []

    def count_chat_input_tokens(self, payload, **kwargs):
        self.call_order.append("count_chat_input_tokens")
        self.count_calls.append({"payload": payload, **kwargs})
        if self.count_error is not None:
            raise self.count_error
        return InputTokenCount(
            self.count,
            InputTokenSupport.SUPPORTED
            if self.count is not None
            else InputTokenSupport.UNSUPPORTED,
        )


def payload_hash(payload):
    return sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def test_explicit_budget_counts_same_final_payload_as_generate_with_history_profile_vision():
    options = dict(
        profile=TaskProfileSnapshot(
            "caption",
            "Caption",
            system_prompt="Profile system",
            prompt_prefix="<",
            prompt_suffix=">",
        ),
        messages=ConversationMessages(
            (TextMessage("user", "Earlier question"), TextMessage("assistant", "Earlier answer"))
        ),
        images=("data:image/png;base64,fixture",),
        structured_output={"type": "json_object"},
        token_ban=[["forbidden", False]],
        thinking_mode="off",
        max_tokens=16,
        seed=73,
        sampling_mode="custom",
        temperature=0.2,
    )
    count_client = BudgetClient()
    count_runner, count_manager, _ = executor(client=count_client)
    observed = count_runner.budget("Inspect", **options)
    generate_client = BudgetClient()
    generate_runner, _, _ = executor(client=generate_client)
    generate_runner.generate("Inspect", budget_policy="report", **options)

    payload = count_client.count_calls[0]["payload"]
    assert payload == generate_client.calls[0]["payload"]
    assert payload["messages"][0] == {"role": "system", "content": "Profile system"}
    assert payload["messages"][1:3] == [
        {"role": "user", "content": "Earlier question"},
        {"role": "assistant", "content": "Earlier answer"},
    ]
    content = payload["messages"][-1]["content"]
    assert next(item["text"] for item in content if item["type"] == "text") == "<Inspect>"
    assert next(
        item["image_url"]["url"] for item in content if item["type"] == "image_url"
    ).endswith("fixture")
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}
    assert payload["temperature"] == 0.2 and payload["seed"] == 73
    assert count_client.count_calls[0]["autoload"] is False
    assert count_client.calls == count_client.prepare_calls == []
    assert count_client.closed
    assert (
        len(count_manager.runtime_service.begin_calls)
        == len(count_manager.runtime_service.finish_calls)
        == 1
    )
    assert observed["payload_sha256"] == payload_hash(payload)
    assert observed["budget"]["status"] == "fit"
    assert observed["budget"]["input_tokens"] == 12
    assert observed["budget"]["context_limit"] == 100
    assert observed["budget"]["remaining"] == 72
    assert observed["release"]["status"] == "not_requested"
    assert "fixture" not in json.dumps(observed)
    assert len(generate_client.count_calls) == 1
    assert payload["stream_options"] == {"include_usage": True}


def test_generate_report_recounts_actual_request_and_retains_budget_in_result():
    client = BudgetClient(count=90, context=100)
    runner, _, _ = executor(client=client)
    observation = runner.budget("Earlier", max_tokens=20)
    _, _, result = runner.generate("Changed", budget_policy="report", max_tokens=20)
    assert len(client.count_calls) == 2
    assert client.count_calls[-1]["payload"] == client.calls[0]["payload"]
    assert result.budget.status.value == "overflow"
    assert result.budget_payload_sha256 == payload_hash(client.calls[0]["payload"])
    assert result.budget_payload_sha256 != observation["payload_sha256"]
    assert GenerationResult.from_json(result.to_json()) == result


@pytest.mark.parametrize(
    "count,context,status",
    [(12, 32, "fit"), (13, 32, "overflow"), (None, 32, "unknown"), (12, None, "unknown")],
)
def test_budget_report_and_enforce_policy(count, context, status):
    client = BudgetClient(count=count, context=context)
    runner, manager, _ = executor(client=client)
    assert runner.budget("Question", max_tokens=20)["budget"]["status"] == status
    if status == "fit":
        assert (
            runner.budget("Question", max_tokens=20, budget_policy="enforce")["budget"]["remaining"]
            == 0
        )
    else:
        with pytest.raises(CanonicalGenerationError) as caught:
            runner.budget("Question", max_tokens=20, budget_policy="enforce")
        assert caught.value.category == ErrorCategory.INVALID_REQUEST
    assert client.calls == client.prepare_calls == []
    assert (
        len(manager.runtime_service.begin_calls) == len(manager.runtime_service.finish_calls) == 2
    )


@pytest.mark.parametrize("count,context", [(90, 100), (None, 100), (1, None)])
def test_generate_enforce_stops_before_stream_when_budget_cannot_fit(count, context):
    client = BudgetClient(count=count, context=context)
    runner, manager, _ = executor(client=client)
    with pytest.raises(CanonicalGenerationError):
        runner.generate("Question", budget_policy="enforce", max_tokens=20)
    assert len(client.count_calls) == 1
    assert client.calls == client.prepare_calls == []
    assert len(manager.runtime_service.finish_calls) == 1


def test_router_budget_resolves_exact_model_and_releases_only_its_lease():
    handle = FakeReleaseHandle(release_result(mode=RuntimeMode.ROUTER))
    service = FakeRuntimeService(handle, mode=RuntimeMode.ROUTER)
    manager = FakeManager(router=True, service=service)
    client = BudgetClient()
    runner, _, _ = executor(manager=manager, client=client)
    observed = runner.budget(
        "Question", model="saved-alias", max_tokens=20, release_after_generation=True
    )
    assert manager.resolve_calls == ["saved-alias"]
    assert client.count_calls[0]["payload"]["model"] == "canonical/model.gguf"
    assert client.props_calls[0]["model"] == "canonical/model.gguf"
    assert service.begin_calls[0]["model_id"] == "canonical/model.gguf"
    assert service.begin_calls[0]["release_after"] is True
    assert len(service.finish_calls) == 1 and len(handle.wait_calls) == 1
    assert observed["release"]["success"] is True
    assert observed["release"]["released_models"] == ["canonical/model.gguf"]
    assert observed["release"]["driver_memory_verified"] is False


@pytest.mark.parametrize(
    "error,expected",
    [
        (OperationCancelled("cancelled"), ErrorCategory.CANCELLED),
        (DeadlineExceeded("deadline"), ErrorCategory.TIMEOUT),
        (ResponseProtocolError("bad count"), ErrorCategory.PROTOCOL),
        (LlamaClientError("auth", status_code=401), ErrorCategory.AUTHENTICATION),
        (LlamaClientError("server", status_code=500), ErrorCategory.SERVER),
    ],
)
def test_failed_count_releases_owned_lease_closes_client_and_emits_terminal_state(error, expected):
    handle = FakeReleaseHandle(release_result())
    service = FakeRuntimeService(handle)
    manager = FakeManager(service=service)
    client = BudgetClient(count_error=error)
    events = []
    registry = LiveGenerationRegistry(sender=lambda _name, payload, _client: events.append(payload))
    identity = ExecutionIdentity.create(
        prompt_id="budget-prompt", node_id="budget-node", client_id="budget-browser"
    )
    runner, _, _ = executor(manager=manager, client=client, live_registry=registry)
    with pytest.raises(CanonicalGenerationError) as caught:
        runner.budget("Question", max_tokens=20, release_after_generation=True, identity=identity)
    assert caught.value.category == expected
    assert len(service.finish_calls) == len(handle.wait_calls) == 1
    assert client.closed and client.calls == client.prepare_calls == []
    assert registry.active_count == 0
    assert events[-1]["terminal"] is True
    assert events[-1]["phase"] == ("cancelled" if expected == ErrorCategory.CANCELLED else "failed")
    assert events[-1]["release"]["success"] is True


def test_count_timeout_budget_decreases_after_context_lookup_and_release():
    client = BudgetClient()
    handle = FakeReleaseHandle(release_result())
    runner, _, _ = executor(manager=FakeManager(service=FakeRuntimeService(handle)), client=client)
    runner.budget("Question", max_tokens=20, request_timeout=10, release_after_generation=True)
    assert (
        0
        < handle.wait_calls[0]
        < client.count_calls[0]["timeout"]
        < client.props_calls[0]["timeout"]
        < 10
    )
    assert callable(client.count_calls[0]["cancel"])
    assert callable(client.props_calls[0]["cancel"])


def test_budget_records_the_epoch_admitted_by_its_runtime_lease():
    class EpochService(FakeRuntimeService):
        def begin_generation(self, **kwargs):
            lease = super().begin_generation(**kwargs)
            lease.runtime_epoch = 71
            return lease

    service = EpochService()
    runner, _, _ = executor(manager=FakeManager(service=service), client=BudgetClient())
    assert runner.budget("Question", max_tokens=20)["runtime_epoch"] == 71


def test_cancel_after_count_returns_no_budget_and_still_finishes_owned_lease(monkeypatch):
    cancelled = False
    client = BudgetClient()
    count = client.count_chat_input_tokens

    def cancel_after_count(*args, **kwargs):
        nonlocal cancelled
        result = count(*args, **kwargs)
        cancelled = True
        return result

    monkeypatch.setattr(client, "count_chat_input_tokens", cancel_after_count)
    handle = FakeReleaseHandle(release_result())
    service = FakeRuntimeService(handle)
    runner, _, _ = executor(manager=FakeManager(service=service), client=client)
    with pytest.raises(CanonicalGenerationError) as caught:
        runner.budget(
            "Question",
            max_tokens=20,
            release_after_generation=True,
            cancel_check=lambda: cancelled,
        )
    assert caught.value.category == ErrorCategory.CANCELLED
    assert client.calls == [] and len(service.finish_calls) == len(handle.wait_calls) == 1


def test_release_failure_never_exposes_successful_budget_output():
    handle = FakeReleaseHandle(release_result(status=ReleaseStatus.FAILED, error="fixture failed"))
    runner, _, client = executor(
        manager=FakeManager(service=FakeRuntimeService(handle)), client=BudgetClient()
    )
    with pytest.raises(CanonicalGenerationError) as caught:
        runner.budget("Question", max_tokens=20, release_after_generation=True)
    assert caught.value.category == ErrorCategory.RELEASE_FAILED
    assert client.closed


def test_attached_budget_does_not_acquire_or_release_local_runtime():
    runner, manager, client = executor(manager=FakeManager(managed=False), client=BudgetClient())
    observed = runner.budget("Question", server_url="http://127.0.0.1:8999", max_tokens=20)
    assert observed["budget"]["status"] == "fit"
    assert manager.runtime_service.begin_calls == manager.runtime_service.finish_calls == []
    assert client.calls == []
    with pytest.raises(CanonicalGenerationError):
        runner.budget("Question", server_url="http://127.0.0.1:8999", release_after_generation=True)


def test_unsupported_props_produces_unknown_context_without_hiding_auth_failure():
    client = BudgetClient(props_error=LlamaClientError("missing", status_code=404))
    runner, _, _ = executor(client=client)
    observed = runner.budget("Question", max_tokens=20)
    assert observed["budget"]["input_tokens"] == 12
    assert observed["budget"]["context_limit"] is None
    assert observed["budget"]["status"] == "unknown"
    auth_client = BudgetClient(props_error=LlamaClientError("auth", status_code=403))
    runner, manager, _ = executor(client=auth_client)
    with pytest.raises(CanonicalGenerationError) as caught:
        runner.budget("Question")
    assert caught.value.category == ErrorCategory.AUTHENTICATION
    assert auth_client.count_calls == [] and len(manager.runtime_service.finish_calls) == 1


def test_budget_node_reuses_generate_preparation_and_returns_typed_observation(
    node_package, monkeypatch
):
    generate = importlib.import_module(f"{node_package.__name__}.nodes.generate")
    budget_module = importlib.import_module(f"{node_package.__name__}.nodes.request_budget")
    schema = budget_module.LlamaCppRequestBudget.INPUT_TYPES()
    assert list(schema["optional"]) == list(generate.LlamaCppGenerate.INPUT_TYPES()["optional"])
    assert list(schema["optional"])[-1] == "budget_policy"
    assert (
        generate.LlamaCppGenerate.INPUT_TYPES()["optional"]["budget_policy"][1]["default"] == "off"
    )
    assert schema["optional"]["budget_policy"][1]["default"] == "report"
    assert budget_module.LlamaCppRequestBudget.generate is generate.LlamaCppGenerate.generate
    captured = {}
    envelope = {
        "schema_version": 1,
        "budget": RequestBudget(12, 100, 20).as_dict(),
        "payload_sha256": "a" * 64,
        "runtime_epoch": 7,
        "release": {"status": "not_requested"},
        "elapsed_seconds": 0.5,
    }

    class BudgetExecutor:
        def budget(self, prompt, **kwargs):
            captured.update(prompt=prompt, **kwargs)
            captured["images"] = kwargs["prepare_images"]()
            return envelope

        def generate(self, *_args, **_kwargs):
            raise AssertionError("count-only node called generation")

    images = object()
    monkeypatch.setattr(budget_module, "CanonicalGenerationExecutor", BudgetExecutor)
    monkeypatch.setattr(
        generate,
        "collect_images",
        lambda amount, inputs, **kwargs: (
            ["encoded"] if amount == 1 and inputs["image_1"] is images else []
        ),
    )
    output = budget_module.LlamaCppRequestBudget().generate(
        "Question",
        max_tokens=20,
        image_amount=1,
        image_1=images,
        budget_policy="report",
    )
    summary, serialized, budget = output["result"]
    assert summary == "FIT: input 12, requested output 20, context 100, remaining 68."
    assert output["ui"] == {"text": (summary,)}
    assert json.loads(serialized) == envelope
    assert isinstance(budget, budget_module.RequestBudget)
    assert captured["images"] == ["encoded"]
    assert captured["prompt"] == "Question"
    assert captured["max_tokens"] == 20
    assert captured["budget_policy"] == "report"
    assert callable(captured["cancel_check"])


def test_default_generate_retains_payload_without_usage_extension_or_count():
    client = BudgetClient()
    runner, _, _ = executor(client=client)
    runner.generate("Question")
    assert "stream_options" not in client.calls[0]["payload"]
    assert client.count_calls == []
