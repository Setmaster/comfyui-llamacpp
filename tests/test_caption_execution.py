"""Independent caption-coordinator contracts using inert in-memory dependencies."""

from __future__ import annotations

import json
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from test_canonical_generation import (
    ClientFactory,
    FakeClient,
    FakeManager,
    FakeReleaseHandle,
    FakeRuntimeService,
    release_result,
    successful_stream,
)
from test_streaming import FakeResponse, data_line

import runtime.client as client_module
from generation.contracts import ErrorCategory, GenerationResult
from generation.execution import CanonicalGenerationError, CanonicalGenerationExecutor
from generation.profiles import TaskProfileSnapshot
from runtime.client import LlamaServerClient, StreamControlProbe, StreamControlSupport
from runtime.live_generation import CancelScope, ExecutionIdentity, LiveGenerationRegistry
from runtime.streaming import StreamResult

IMAGES = (
    "data:image/png;base64,Zmlyc3Q=",
    "data:image/png;base64,c2Vjb25k",
    "data:image/png;base64,dGhpcmQ=",
)


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class Control:
    def __init__(self):
        self.deletes = 0

    def delete(self):
        self.deletes += 1
        return True


class BatchClient(FakeClient):
    def __init__(self, streams=None, *, on_request=None):
        super().__init__(props=SimpleNamespace(modalities={"vision": True}))
        self.streams = (
            list(streams)
            if streams is not None
            else [
                successful_stream(f"caption-{index}", thinking="", model="vision-model")
                for index in range(3)
            ]
        )
        self.controls = []
        self.on_request = on_request

    def prepare_stream_control(self, **kwargs):
        self.prepare_calls.append(kwargs)
        control = Control()
        self.controls.append(control)
        return StreamControlProbe(StreamControlSupport.SUPPORTED), control

    def stream_chat(self, payload, **kwargs):
        index = len(self.calls)
        if self.on_request:
            self.on_request(index, payload, kwargs)
        result = self.streams[index]
        if isinstance(result, BaseException):
            self.calls.append({"payload": payload, **kwargs})
            raise result
        self.result = result
        return super().stream_chat(payload, **kwargs)


def setup_executor(*, client=None, manager=None, clock=None, registry=None, factory=None):
    handle = FakeReleaseHandle(release_result())
    manager = manager or FakeManager(service=FakeRuntimeService(handle))
    client = client or BatchClient()
    factory = factory or ClientFactory(client)
    runner = CanonicalGenerationExecutor(
        manager=manager, client_factory=factory, clock=clock or Clock(), live_registry=registry
    )
    return runner, manager.runtime_service, client, handle, factory


def images(check):
    assert callable(check)
    check()
    return list(IMAGES)


def caption(runner, **kwargs):
    return runner.captions("Describe", item_count=3, prepare_images=images, **kwargs)


def assert_finished_once(service, handle):
    assert len(service.begin_calls) == 1
    assert len(service.finish_calls) == 1
    assert service.finish_calls[0].release_after is True
    assert len(handle.wait_calls) == 1


def test_per_image_payloads_preserve_ids_prompts_seeds_and_release_only_once():
    runner, service, client, handle, factory = setup_executor()
    profile = TaskProfileSnapshot(
        "format", "Format", system_prompt="system", prompt_prefix="[", prompt_suffix="]"
    )
    captions, encoded = caption(
        runner,
        ids_json='[" third ","first","second"]',
        prompts_json='["  object ","color","position\\n"]',
        seeds_json="[19,7,31]",
        profile=profile,
        release_after_generation=True,
    )
    assert captions == ["caption-0", "caption-1", "caption-2"]
    assert len(factory.connections) == 1
    assert len(client.calls) == 3
    assert len(client.controls) == 3
    assert len({id(control) for control in client.controls}) == 3
    for index, call in enumerate(client.calls):
        assert call["stream_control"] is client.controls[index]
        payload = call["payload"]
        assert payload["stream"] is True
        assert payload["stream_options"] == {"include_usage": True}
        assert payload["seed"] == [19, 7, 31][index]
        assert payload["messages"] == [
            {"role": "system", "content": "system"},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": IMAGES[index]}},
                    {"type": "text", "text": ["[  object ]", "[color]", "[position\n]"][index]},
                ],
            },
        ]
    envelope = json.loads(encoded)
    assert set(envelope) == {"schema_version", "operation", "state", "rows", "release", "warnings"}
    assert (envelope["schema_version"], envelope["operation"], envelope["state"]) == (
        1,
        "captions",
        "complete",
    )
    assert [row["item_id"] for row in envelope["rows"]] == [" third ", "first", "second"]
    assert [row["seed"] for row in envelope["rows"]] == [19, 7, 31]
    for row in envelope["rows"]:
        assert set(row) == {"item_id", "seed", "state", "response", "thinking", "result", "error"}
        assert row["state"] == "complete"
        assert row["error"] is None
        result = GenerationResult.from_dict(row["result"])
        assert result.success is True
        assert result.image_count == 1
        assert result.effective_model == "vision-model"
        assert result.release.policy.value == "reuse"
    assert envelope["release"]["success"] is True
    assert envelope["release"]["terminal"] is True
    assert all(image not in encoded for image in IMAGES)
    assert_finished_once(service, handle)
    assert client.closed is True


@pytest.mark.parametrize("partial_policy", ["raise_error", "return_marked_partial"])
def test_middle_item_failure_stops_batch_and_keeps_alignment(partial_policy):
    client = BatchClient(
        [
            successful_stream("first complete", thinking="", model="vision-model"),
            StreamResult("unfinished second", "", False, error_type="transport", partial=True),
            successful_stream("must never run", model="vision-model"),
        ]
    )
    runner, service, _, handle, _ = setup_executor(client=client)
    if partial_policy == "raise_error":
        with pytest.raises(CanonicalGenerationError) as caught:
            caption(runner, partial_output_policy=partial_policy, release_after_generation=True)
        assert caught.value.category == ErrorCategory.TRANSPORT
    else:
        captions, encoded = caption(
            runner, partial_output_policy=partial_policy, release_after_generation=True
        )
        assert captions == ["first complete", "", ""]
        envelope = json.loads(encoded)
        assert envelope["state"] == "partial"
        assert [row["item_id"] for row in envelope["rows"]] == ["0", "1", "2"]
        assert [row["state"] for row in envelope["rows"]] == ["complete", "failed", "not_attempted"]
        assert envelope["rows"][1]["error"]["category"] == "transport"
        assert envelope["rows"][2] == {
            "item_id": "2",
            "seed": 0,
            "state": "not_attempted",
            "response": "",
            "thinking": "",
            "result": None,
            "error": None,
        }
    assert len(client.calls) == 2
    assert_finished_once(service, handle)
    assert client.closed is True


def test_exact_cancel_during_second_item_does_not_cancel_unrelated_execution():
    events = []
    registry = LiveGenerationRegistry(sender=lambda _name, payload, _client: events.append(payload))
    identity = ExecutionIdentity.create(prompt_id="batch", node_id="5", client_id="fixture")
    unrelated_identity = ExecutionIdentity.create(
        prompt_id="other", node_id="6", client_id="other-client"
    )
    unrelated = registry.begin(
        unrelated_identity,
        cancel_scope=CancelScope.GENERATION,
        cancel_callback=lambda: pytest.fail("unrelated delete"),
    )

    def cancel(index, _payload, kwargs):
        if index == 1:
            assert (
                registry.cancel(identity.execution_id, prompt_id="wrong", node_id="5").accepted
                is False
            )
            assert kwargs["cancel"]() is False
            registry.cancel(identity.execution_id, prompt_id="batch", node_id="5")
            assert kwargs["cancel"]() is True

    client = BatchClient(
        [
            successful_stream("first", thinking="", model="vision-model"),
            StreamResult(
                "partial", "", False, error_type="cancelled", cancelled=True, partial=True
            ),
        ],
        on_request=cancel,
    )
    runner, service, _, handle, _ = setup_executor(client=client, registry=registry)
    try:
        captions, encoded = caption(
            runner,
            identity=identity,
            partial_output_policy="return_marked_partial",
            release_after_generation=True,
        )
        assert captions == ["first", "", ""]
        envelope = json.loads(encoded)
        assert envelope["state"] == "cancelled"
        assert envelope["rows"][1]["error"]["category"] == "cancelled"
        assert len(client.calls) == 2
        assert [control.deletes for control in client.controls] == [0, 1]
        assert unrelated.token.cancelled is False
        assert registry.snapshot(unrelated_identity.execution_id) is not None
        batch_events = [
            event for event in events if event["execution_id"] == str(identity.execution_id)
        ]
        assert len([event for event in batch_events if event["terminal"]]) == 1
        assert batch_events[-1]["phase"] == "cancelled"
        assert_finished_once(service, handle)
        assert handle.wait_cancels == [None]
    finally:
        unrelated.finish(phase="complete")


def test_overall_deadline_charges_preparation_and_every_item_before_release():
    clock = Clock()

    def prepare(check):
        clock.advance(2)
        check()
        return list(IMAGES)

    def tick(_index, _payload, _kwargs):
        clock.advance(2)

    client = BatchClient(on_request=tick)
    runner, service, _, handle, _ = setup_executor(client=client, clock=clock)
    runner.captions(
        "Describe",
        item_count=3,
        prepare_images=prepare,
        request_timeout=10,
        release_after_generation=True,
    )
    assert [call["timeout"] for call in client.calls] == [8, 6, 4]
    assert handle.wait_calls == [2]
    assert_finished_once(service, handle)


def test_release_failure_withholds_even_completed_rows():
    runner, service, client, handle, _ = setup_executor()
    handle.error = TimeoutError("fixture release failure")
    with pytest.raises(CanonicalGenerationError) as caught:
        caption(
            runner, partial_output_policy="return_marked_partial", release_after_generation=True
        )
    assert caught.value.category == ErrorCategory.RELEASE_FAILED
    assert len(client.calls) == 3
    assert_finished_once(service, handle)
    assert client.closed is True


def test_base_exception_never_leaks_lease_or_returns_partial_rows():
    class ComfyInterrupt(BaseException):
        pass

    client = BatchClient([successful_stream("first", model="vision-model"), ComfyInterrupt()])
    runner, service, _, handle, _ = setup_executor(client=client)
    with pytest.raises(ComfyInterrupt):
        caption(
            runner, partial_output_policy="return_marked_partial", release_after_generation=True
        )
    assert len(service.begin_calls) == len(service.finish_calls) == 1
    assert handle.wait_calls == []
    assert client.closed is True


def test_repeated_requests_use_real_sse_parser_for_each_independent_image(monkeypatch):
    monkeypatch.setattr(client_module, "_STREAM_CONTROL_CACHE", {})

    class HTTP:
        def __init__(self):
            self.payloads = []
            self.responses = []

        def request(self, method, url, **kwargs):
            path = urlsplit(url).path
            if (method, path) == ("GET", "/props"):
                response = FakeResponse(payload={"modalities": {"vision": True}})
            elif (method, path) == ("POST", "/v1/streams/lookup"):
                response = FakeResponse(status_code=404, payload={})
            elif (method, path) == ("POST", "/v1/chat/completions"):
                self.payloads.append(kwargs["json"])
                response = FakeResponse(
                    [
                        data_line(
                            {
                                "model": "vision-model",
                                "choices": [
                                    {
                                        "delta": {"content": f"SSE-{len(self.payloads)}"},
                                        "finish_reason": "stop",
                                    }
                                ],
                            }
                        ),
                        "",
                        "data: [DONE]",
                        "",
                    ]
                )
            else:
                raise AssertionError(f"unexpected fixture route {method} {path}")
            self.responses.append(response)
            return response

    http = HTTP()
    clients = []

    def factory(connection):
        client = LlamaServerClient(connection, session=http)
        clients.append(client)
        return client

    runner, service, _, handle, _ = setup_executor(factory=factory)
    captions, encoded = caption(runner, release_after_generation=True)
    assert captions == ["SSE-1", "SSE-2", "SSE-3"]
    assert [
        payload["messages"][0]["content"][0]["image_url"]["url"] for payload in http.payloads
    ] == list(IMAGES)
    assert all(row["result"]["done_received"] for row in json.loads(encoded)["rows"])
    assert len(clients) == 1
    assert all(response.closed for response in http.responses)
    assert_finished_once(service, handle)


@pytest.mark.parametrize(
    "options",
    [
        {"item_count": 0},
        {"item_count": 33},
        {"item_count": True},
        {"ids_json": '["a","a","b"]'},
        {"ids_json": '["a"]'},
        {"prompts_json": '["only one"]'},
        {"seeds_json": "[1,2]"},
        {"seeds_json": "[1,true,3]"},
    ],
)
def test_invalid_alignment_fails_before_preparation_or_client(options):
    runner, service, client, _, factory = setup_executor()

    def forbidden_prepare(_check):
        pytest.fail("invalid caption alignment must not prepare images")

    arguments = {"item_count": 3, "prepare_images": forbidden_prepare, **options}
    with pytest.raises((TypeError, ValueError)):
        runner.captions("Describe", **arguments)
    assert factory.connections == []
    assert service.begin_calls == service.finish_calls == client.calls == []


@pytest.mark.parametrize("case", ["count", "bytes", "expired"])
def test_preparation_rejects_mismatched_or_oversized_images_and_observes_deadline(
    monkeypatch, case
):
    import generation.execution as execution_module

    clock = Clock()
    runner, service, client, _, factory = setup_executor(clock=clock)
    if case == "bytes":
        monkeypatch.setattr(execution_module, "MAX_CAPTION_IMAGE_BYTES", 16)

    def prepare(check):
        if case == "expired":
            clock.advance(5)
        check()
        return list(IMAGES[:2] if case == "count" else IMAGES)

    with pytest.raises(CanonicalGenerationError) as caught:
        runner.captions("Describe", item_count=3, prepare_images=prepare, request_timeout=5)
    assert caught.value.category == (
        ErrorCategory.TIMEOUT if case == "expired" else ErrorCategory.INVALID_REQUEST
    )
    assert factory.connections == []
    assert service.begin_calls == service.finish_calls == client.calls == []


def test_deadline_exhausted_after_first_item_stops_before_second_request():
    clock = Clock()
    client = BatchClient(on_request=lambda *_args: clock.advance(5))
    runner, service, _, handle, _ = setup_executor(client=client, clock=clock)
    captions, encoded = caption(
        runner,
        request_timeout=5,
        partial_output_policy="return_marked_partial",
        release_after_generation=True,
    )
    assert captions == ["caption-0", "", ""]
    envelope = json.loads(encoded)
    assert envelope["state"] == "partial"
    assert envelope["rows"][1]["error"]["category"] == "timeout"
    assert envelope["rows"][2]["state"] == "not_attempted"
    assert len(client.calls) == 1
    assert handle.wait_calls == [0]
    assert_finished_once(service, handle)


def test_first_item_failure_returns_failed_group_only_when_partial_explicit():
    client = BatchClient([StreamResult("", "", False, error_type="transport")])
    runner, service, _, handle, _ = setup_executor(client=client)
    captions, encoded = caption(
        runner, partial_output_policy="return_marked_partial", release_after_generation=True
    )
    assert captions == ["", "", ""]
    envelope = json.loads(encoded)
    assert envelope["state"] == "failed"
    assert [row["state"] for row in envelope["rows"]] == [
        "failed",
        "not_attempted",
        "not_attempted",
    ]
    assert len(client.calls) == 1
    assert_finished_once(service, handle)


def test_each_item_resets_live_preview_but_keeps_one_execution_identity():
    events = []
    registry = LiveGenerationRegistry(sender=lambda _name, payload, _client: events.append(payload))
    identity = ExecutionIdentity.create(prompt_id="live-batch", node_id="7", client_id="fixture")
    observed = []

    class InspectClient(BatchClient):
        def stream_chat(self, payload, **kwargs):
            before = registry.snapshot(identity.execution_id)
            assert before.response.text == ""
            assert before.thinking.text == ""
            assert before.chunks == 0
            result = super().stream_chat(payload, **kwargs)
            after = registry.snapshot(identity.execution_id)
            observed.append((after.response.text, after.thinking.text))
            return result

    client = InspectClient(
        [
            successful_stream("first", thinking="first thought", model="vision-model"),
            successful_stream("second", thinking="second thought", model="vision-model"),
            successful_stream("third", thinking="third thought", model="vision-model"),
        ]
    )
    runner, _, _, _, _ = setup_executor(client=client, registry=registry)
    caption(runner, identity=identity)
    assert observed == [
        ("first", "first thought"),
        ("second", "second thought"),
        ("third", "third thought"),
    ]
    assert {event["execution_id"] for event in events} == {str(identity.execution_id)}
    assert len([event for event in events if event["terminal"]]) == 1
    assert events[-1]["phase"] == "complete"
    assert events[-1]["response"]["text"] == "third"
    assert registry.snapshot(identity.execution_id) is None


def test_router_resolves_once_and_requires_same_exact_model_for_every_row():
    manager = FakeManager(router=True, canonical_model="exact/router-id")
    client = BatchClient(
        [
            successful_stream("first", thinking="", model="exact/router-id"),
            successful_stream("wrong model response", thinking="", model="other-id"),
        ]
    )
    runner, service, _, _, _ = setup_executor(client=client, manager=manager)
    captions, encoded = caption(
        runner, model="saved/model.gguf", partial_output_policy="return_marked_partial"
    )
    assert captions == ["first", "", ""]
    assert manager.resolve_calls == ["saved/model.gguf"]
    assert len(service.atomic_calls) == 1
    assert len(service.finish_calls) == 1
    assert [call["payload"]["model"] for call in client.calls] == ["exact/router-id"] * 2
    rows = json.loads(encoded)["rows"]
    assert rows[0]["result"]["effective_model"] == "exact/router-id"
    assert rows[1]["error"]["category"] == "protocol"
    assert rows[1]["response"] == ""
    assert rows[2]["state"] == "not_attempted"


def test_structured_constraints_and_token_bans_apply_to_each_independent_request():
    client = BatchClient(
        [
            successful_stream('{"caption":"first"}', thinking="", model="vision-model"),
            successful_stream("not JSON", thinking="", model="vision-model"),
        ]
    )
    runner, service, _, handle, _ = setup_executor(client=client)
    captions, encoded = caption(
        runner,
        structured_output={"type": "json_object"},
        token_ban=[["banned", False]],
        partial_output_policy="return_marked_partial",
        release_after_generation=True,
    )
    assert captions == ['{"caption":"first"}', "", ""]
    assert json.loads(encoded)["rows"][1]["error"]["category"] == "structured_json"
    for call in client.calls:
        assert call["payload"]["response_format"] == {"type": "json_object"}
        assert call["payload"]["logit_bias"] == [["banned", False]]
    assert_finished_once(service, handle)


def test_aggregate_evidence_limit_stops_before_next_item_and_still_releases(monkeypatch):
    import generation.captions as captions_module
    import generation.execution as execution_module

    limit = 70_000
    monkeypatch.setattr(captions_module, "MAX_CAPTION_RESULT_BYTES", limit)
    monkeypatch.setattr(execution_module, "MAX_CAPTION_RESULT_BYTES", limit)
    client = BatchClient([successful_stream("x" * 5000, thinking="", model="vision-model")])
    runner, service, _, handle, _ = setup_executor(client=client)
    captions, encoded = caption(
        runner, partial_output_policy="return_marked_partial", release_after_generation=True
    )
    assert captions == ["", "", ""]
    assert len(encoded.encode("utf-8")) <= limit
    rows = json.loads(encoded)["rows"]
    assert [row["state"] for row in rows] == ["failed", "not_attempted", "not_attempted"]
    assert "limit" in rows[0]["error"]["message"]
    assert len(client.calls) == 1
    assert_finished_once(service, handle)


@pytest.mark.parametrize("policy", ["raise_error", "return_marked_partial"])
@pytest.mark.parametrize("release_during_probe", [False, True])
def test_external_global_release_stops_later_caption_submissions(policy, release_during_probe):
    from test_runtime_service import FakeProcessController

    from runtime.service import ReleaseStatus, RuntimeService

    process = FakeProcessController()
    service = RuntimeService(process)
    service.configure_direct_owned()
    manager = FakeManager()
    manager.runtime_service = service

    def release(index, *_):
        if index == 0:
            assert service.request_release(source="comfy_free").status == ReleaseStatus.DEFERRED

    client = BatchClient(on_request=None if release_during_probe else release)
    if release_during_probe:
        prepare = client.prepare_stream_control

        def prepare_and_release(**kwargs):
            result = prepare(**kwargs)
            release(0)
            return result

        client.prepare_stream_control = prepare_and_release
    runner, *_ = setup_executor(manager=manager, client=client)
    if policy == "raise_error":
        with pytest.raises(CanonicalGenerationError) as error:
            caption(runner, partial_output_policy=policy, release_after_generation=True)
        assert error.value.category == ErrorCategory.CANCELLED
    else:
        values, encoded = caption(
            runner, partial_output_policy=policy, release_after_generation=True
        )
        result = json.loads(encoded)
        assert result["state"] == "cancelled"
        assert result["release"]["terminal"] is True
        assert result["release"]["success"] is True
        if release_during_probe:
            assert values == ["", "", ""]
            assert [row["state"] for row in result["rows"]] == [
                "failed",
                "not_attempted",
                "not_attempted",
            ]
        else:
            assert values == ["caption-0", "", ""]
            assert [row["state"] for row in result["rows"]] == [
                "complete",
                "failed",
                "not_attempted",
            ]
    assert len(client.calls) == (0 if release_during_probe else 1)
    assert process.stop_calls == 1
    assert service.active_generations == 0
    assert service.release_pending is False


def test_own_group_release_intent_does_not_stop_later_caption_submissions():
    from test_runtime_service import FakeProcessController

    from runtime.service import RuntimeService

    process = FakeProcessController()
    service = RuntimeService(process)
    service.configure_direct_owned()
    client = BatchClient()
    manager = FakeManager()
    manager.runtime_service = service
    runner, *_ = setup_executor(manager=manager, client=client)
    values, encoded = caption(runner, release_after_generation=True)
    assert values == ["caption-0", "caption-1", "caption-2"]
    assert json.loads(encoded)["release"]["success"] is True
    assert process.stop_calls == 1
    assert service.active_generations == 0
