"""Coordinator-to-SSE transcription checks with inert files and in-memory HTTP."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import wave
from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import urlsplit

import numpy as np
import pytest
import requests
from test_canonical_generation import (
    ClientFactory,
    FakeClient,
    FakeLease,
    FakeManager,
    FakeReleaseHandle,
    FakeRuntimeService,
    release_result,
    successful_stream,
)
from test_streaming import FakeResponse, data_line

import generation.execution as execution_module
import runtime.client as client_module
from generation.audio import (
    APPROVED_MODEL,
    APPROVED_PROJECTOR,
    SUPPORTED_ASR_PAIR,
    VERIFIED_ASR_PREFIX,
    ApprovedPairIdentity,
    AudioPreparationCancelled,
    UnsupportedAudioModelError,
)
from generation.contracts import ErrorCategory, GenerationState
from generation.execution import CanonicalGenerationError, CanonicalGenerationExecutor
from runtime.client import LlamaServerClient, StreamControlProbe, StreamControlSupport
from runtime.live_generation import ExecutionIdentity, LiveGenerationRegistry
from runtime.service import RuntimeMode
from runtime.streaming import StreamResult


def audio_clip():
    return {
        "waveform": np.array([-0.5, 0.0, 0.5], dtype=np.float32).reshape(1, 1, 3),
        "sample_rate": 16_000,
    }


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class AudioHTTP:
    """Only explicitly expected fixture endpoints exist; no socket is opened."""

    def __init__(self, *, response=None, modalities=None):
        self.stream = response or FakeResponse(
            [
                data_line({"choices": [{"delta": {"content": VERIFIED_ASR_PREFIX}}]}),
                "",
                data_line(
                    {
                        "choices": [
                            {"delta": {"content": " hello café\n"}, "finish_reason": "stop"}
                        ],
                        "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
                    }
                ),
                "",
                "data: [DONE]",
                "",
            ]
        )
        self.modalities = {"audio": True} if modalities is None else modalities
        self.calls = []
        self.responses = []

    def request(self, method, url, **kwargs):
        path = urlsplit(url).path
        self.calls.append((method, path, kwargs))
        if (method, path) == ("GET", "/props"):
            result = FakeResponse(payload={"modalities": self.modalities})
        elif (method, path) == ("POST", "/v1/streams/lookup"):
            result = FakeResponse(status_code=404, payload={})
        elif (method, path) == ("POST", "/v1/chat/completions"):
            result = self.stream
        else:
            raise AssertionError(f"unexpected fixture request {method} {path}")
        self.responses.append(result)
        return result


@pytest.fixture
def approved_pair(monkeypatch, tmp_path):
    """Stub only the expensive hash verifier, retaining its actual approved identity."""
    config = SimpleNamespace(
        model_path=tmp_path / "model.gguf", mmproj_path=tmp_path / "projector.gguf"
    )
    calls = []

    def verify(model, projector, **kwargs):
        assert (model, projector) == (config.model_path, config.mmproj_path)
        assert kwargs["supported_pair"] == SUPPORTED_ASR_PAIR
        assert kwargs["cancel_check"]() is False
        kwargs["deadline_check"]()
        calls.append(kwargs)
        return ApprovedPairIdentity(APPROVED_MODEL.sha256, APPROVED_PROJECTOR.sha256)

    monkeypatch.setattr(execution_module, "verify_approved_pair", verify)
    monkeypatch.setattr(client_module, "_STREAM_CONTROL_CACHE", {})
    return config, calls


def setup_executor(
    approved_pair, *, client=None, manager=None, http=None, clock=None, registry=None
):
    config, _ = approved_pair
    handle = FakeReleaseHandle(release_result())
    manager = manager or FakeManager(service=FakeRuntimeService(handle))
    manager.current_config = config
    if http is not None:

        def factory(connection):
            return LlamaServerClient(connection, session=http)
    else:
        client = client or FakeClient(
            successful_stream(VERIFIED_ASR_PREFIX + "hello", thinking=""),
            props=SimpleNamespace(modalities={"audio": True}),
        )
        factory = ClientFactory(client)
    runner = CanonicalGenerationExecutor(
        manager=manager, client_factory=factory, clock=clock or Clock(), live_registry=registry
    )
    return runner, manager.runtime_service, client, handle


def assert_finished_once(service):
    assert len(service.begin_calls) == 1
    assert len(service.finish_calls) == 1
    assert service.finish_calls[0].release_after is True


def test_audio_only_request_runs_real_http_sse_parser_and_keeps_payload_out_of_provenance(
    approved_pair,
):
    http = AudioHTTP()
    runner, service, _, handle = setup_executor(approved_pair, http=http)
    source = audio_clip()
    original = source["waveform"].copy()
    transcript, raw, language, result, metadata_json = runner.transcribe(
        source, release_after_generation=True
    )
    assert transcript == " hello café\n"
    assert raw == VERIFIED_ASR_PREFIX + transcript
    assert language == "English"
    assert result.response == raw
    assert result.state == GenerationState.COMPLETE
    assert result.done_received is True
    assert result.usage.total_tokens == 12
    assert result.image_count == 0
    assert result.seed == 0
    assert [(method, path) for method, path, _ in http.calls] == [
        ("GET", "/props"),
        ("POST", "/v1/streams/lookup"),
        ("POST", "/v1/chat/completions"),
    ]
    assert http.calls[0][2]["params"] == {"autoload": "false"}
    payload = http.calls[-1][2]["json"]
    assert payload["stream"] is True
    assert payload["temperature"] == 0.0
    assert payload["max_tokens"] == 128
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}
    assert payload["seed"] == 0
    assert len(payload["messages"]) == 1
    message = payload["messages"][0]
    assert message["role"] == "user"
    assert len(message["content"]) == 1
    part = message["content"][0]
    assert part["type"] == "input_audio"
    assert part["input_audio"]["format"] == "wav"
    encoded = part["input_audio"]["data"]
    wav_bytes = base64.b64decode(encoded, validate=True)
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
        assert (wav.getnchannels(), wav.getframerate(), wav.getsampwidth()) == (1, 16000, 2)
        assert wav.getnframes() == 3
    metadata = json.loads(metadata_json)
    assert metadata["kind"] == "transcribe"
    assert metadata["wav_sha256"] == hashlib.sha256(wav_bytes).hexdigest()
    assert metadata["model_sha256"] == APPROVED_MODEL.sha256
    assert metadata["projector_sha256"] == APPROVED_PROJECTOR.sha256
    assert metadata["prefix_status"] == "verified_english"
    assert metadata["warnings"] == []
    serialized = result.to_json() + metadata_json
    assert encoded not in serialized
    assert "input_audio" not in serialized
    assert str(approved_pair[0].model_path) not in serialized
    assert "waveform" not in serialized
    assert np.array_equal(source["waveform"], original)
    assert all(response.closed for response in http.responses)
    assert_finished_once(service)
    assert len(handle.wait_calls) == 1
    assert result.release.success is True
    assert len(approved_pair[1]) == 1


@pytest.mark.parametrize("modalities", [{}, {"audio": False}, {"audio": 1}, {"audio": "true"}])
def test_missing_or_nonboolean_audio_capability_rejects_before_prompt_http(
    approved_pair, modalities
):
    http = AudioHTTP(modalities=modalities)
    runner, service, _, handle = setup_executor(approved_pair, http=http)
    with pytest.raises(CanonicalGenerationError) as caught:
        runner.transcribe(audio_clip(), release_after_generation=True)
    assert caught.value.category == ErrorCategory.CAPABILITY_UNSUPPORTED
    assert [(method, path) for method, path, _ in http.calls] == [("GET", "/props")]
    assert_finished_once(service)
    assert len(handle.wait_calls) == 1


@pytest.mark.parametrize("failure", ["wrong_pair", "missing_projector", "changed_digest"])
def test_invalid_pair_cannot_reach_any_http(approved_pair, monkeypatch, failure):
    http = AudioHTTP()
    runner, service, _, handle = setup_executor(approved_pair, http=http)
    options = {}
    if failure == "wrong_pair":
        options["supported_pair"] = "unsupported pair"
    elif failure == "missing_projector":
        approved_pair[0].mmproj_path = None
    else:

        def reject(*args, **kwargs):
            raise UnsupportedAudioModelError(
                "configured ASR artifact does not match the approved SHA-256"
            )

        monkeypatch.setattr(execution_module, "verify_approved_pair", reject)
    with pytest.raises(CanonicalGenerationError) as caught:
        runner.transcribe(audio_clip(), release_after_generation=True, **options)
    assert caught.value.category == ErrorCategory.CAPABILITY_UNSUPPORTED
    assert http.calls == []
    if failure == "wrong_pair":
        assert service.begin_calls == service.finish_calls == handle.wait_calls == []
    else:
        assert_finished_once(service)
        assert len(handle.wait_calls) == 1


def test_attached_runtime_has_no_audio_or_prompt_http(approved_pair):
    http = AudioHTTP()
    runner, service, _, _ = setup_executor(
        approved_pair, manager=FakeManager(managed=False), http=http
    )
    with pytest.raises(CanonicalGenerationError) as caught:
        runner.transcribe(audio_clip())
    assert caught.value.category == ErrorCategory.CAPABILITY_UNSUPPORTED
    assert http.calls == []
    assert service.begin_calls == service.finish_calls == []
    assert approved_pair[1] == []


def test_admitted_router_mode_is_rejected_before_identity_or_http(approved_pair):
    class RouterService(FakeRuntimeService):
        def resolve_and_begin_generation(self, **kwargs):
            self.begin_generation(release_after=kwargs["release_after"])
            return None, FakeLease(None, kwargs["release_after"], RuntimeMode.ROUTER)

    http = AudioHTTP()
    service = RouterService()
    runner, _, _, _ = setup_executor(approved_pair, manager=FakeManager(service=service), http=http)
    with pytest.raises(CanonicalGenerationError) as caught:
        runner.transcribe(audio_clip())
    assert caught.value.category == ErrorCategory.CAPABILITY_UNSUPPORTED
    assert http.calls == []
    assert len(service.finish_calls) == 1
    assert approved_pair[1] == []


@pytest.mark.parametrize(
    ("stream", "category"),
    [
        (successful_stream("", thinking=""), ErrorCategory.PROTOCOL),
        (successful_stream(VERIFIED_ASR_PREFIX, thinking=""), ErrorCategory.PROTOCOL),
        (replace(successful_stream("partial"), finish_reason="length"), ErrorCategory.PROTOCOL),
        (
            StreamResult("partial", "", False, partial=True, error_type="incomplete"),
            ErrorCategory.PROTOCOL,
        ),
        (
            StreamResult("partial", "", False, partial=True, error_type="transport"),
            ErrorCategory.TRANSPORT,
        ),
        (
            StreamResult(
                "partial", "", False, partial=True, cancelled=True, error_type="cancelled"
            ),
            ErrorCategory.CANCELLED,
        ),
    ],
)
def test_failed_transcripts_never_return_partial_text_and_release_once(
    approved_pair, stream, category
):
    events = []
    registry = LiveGenerationRegistry(sender=lambda _name, payload, _client: events.append(payload))
    client = FakeClient(stream, props=SimpleNamespace(modalities={"audio": True}))
    runner, service, _, handle = setup_executor(approved_pair, client=client, registry=registry)
    identity = ExecutionIdentity.create(
        prompt_id="transcription-job", node_id="7", client_id="fixture"
    )
    with pytest.raises(CanonicalGenerationError) as caught:
        runner.transcribe(audio_clip(), release_after_generation=True, identity=identity)
    assert caught.value.category == category
    assert_finished_once(service)
    assert len(handle.wait_calls) == 1
    assert client.closed is True
    assert events[-1]["terminal"] is True
    assert events[-1]["error"]["category"] == category.value
    assert events[-1]["execution_id"] == str(identity.execution_id)


def test_comfy_base_exception_finishes_lease_once_without_waiting_or_losing_identity(approved_pair):
    class ComfyInterrupt(BaseException):
        pass

    events = []
    registry = LiveGenerationRegistry(sender=lambda _name, payload, _client: events.append(payload))
    client = FakeClient(error=ComfyInterrupt(), props=SimpleNamespace(modalities={"audio": True}))
    runner, service, _, handle = setup_executor(approved_pair, client=client, registry=registry)
    identity = ExecutionIdentity.create(
        prompt_id="interrupted-job", node_id="8", client_id="fixture"
    )
    with pytest.raises(ComfyInterrupt):
        runner.transcribe(audio_clip(), release_after_generation=True, identity=identity)
    assert_finished_once(service)
    assert handle.wait_calls == []
    assert client.closed is True
    assert events[-1]["phase"] == "cancelled"
    assert events[-1]["execution_id"] == str(identity.execution_id)
    assert events[-1]["release"]["status"] == "accepted_continuing"


@pytest.mark.parametrize("stage", ["preparation", "identity"])
def test_cancellation_before_submission_has_cancelled_terminal_feedback(
    approved_pair, monkeypatch, stage
):
    events = []
    registry = LiveGenerationRegistry(sender=lambda _name, payload, _client: events.append(payload))
    target = "prepare_audio" if stage == "preparation" else "verify_approved_pair"
    original = getattr(execution_module, target)
    cancelled = False

    def cancel(*args, **kwargs):
        nonlocal cancelled
        cancelled = True
        assert kwargs["cancel_check"]() is True
        if stage == "preparation":
            return original(*args, **kwargs)
        raise AudioPreparationCancelled("audio preparation was cancelled")

    monkeypatch.setattr(execution_module, target, cancel)
    http = AudioHTTP()
    runner, service, _, handle = setup_executor(approved_pair, http=http, registry=registry)
    identity = ExecutionIdentity.create(prompt_id="cancelled-job", node_id="9", client_id="fixture")
    with pytest.raises(CanonicalGenerationError) as caught:
        runner.transcribe(
            audio_clip(),
            identity=identity,
            release_after_generation=True,
            cancel_check=lambda: cancelled,
        )
    assert caught.value.category == ErrorCategory.CANCELLED
    assert http.calls == []
    assert events[-1]["terminal"] is True
    assert events[-1]["error"]["category"] == "cancelled"
    assert events[-1]["phase"] == "cancelled"
    if stage == "preparation":
        assert service.begin_calls == service.finish_calls == handle.wait_calls == []
    else:
        assert_finished_once(service)
        assert len(handle.wait_calls) == 1


def test_exact_live_cancel_is_not_reused_as_release_wait_cancellation(approved_pair):
    registry = LiveGenerationRegistry()
    identity = ExecutionIdentity.create(prompt_id="exact-job", node_id="10")
    deletions = []

    class Control:
        def delete(self):
            deletions.append(identity.execution_id)
            return True

    class CancelClient(FakeClient):
        def stream_chat(self, payload, **kwargs):
            assert payload["messages"][0]["content"][0]["type"] == "input_audio"
            registry.cancel(identity.execution_id, prompt_id="exact-job", node_id="10")
            assert kwargs["cancel"]() is True
            return StreamResult(
                "unfinished", "", False, error_type="cancelled", cancelled=True, partial=True
            )

    client = CancelClient(
        props=SimpleNamespace(modalities={"audio": True}),
        probe=StreamControlProbe(StreamControlSupport.SUPPORTED),
        stream_control=Control(),
    )
    runner, service, _, handle = setup_executor(approved_pair, client=client, registry=registry)
    with pytest.raises(CanonicalGenerationError) as caught:
        runner.transcribe(audio_clip(), identity=identity, release_after_generation=True)
    assert caught.value.category == ErrorCategory.CANCELLED
    assert deletions == [identity.execution_id]
    assert_finished_once(service)
    assert handle.wait_cancels == [None]
    assert client.closed is True


@pytest.mark.parametrize("case", ["empty", "prefix_only", "length", "unterminated"])
def test_real_sse_failure_never_returns_transcript(approved_pair, case):
    text = {"empty": "", "prefix_only": VERIFIED_ASR_PREFIX}.get(case, "partial transcript")
    finish = "length" if case == "length" else "stop"
    chunk = {"choices": [{"delta": {"content": text}}]}
    if case != "unterminated":
        chunk["choices"][0]["finish_reason"] = finish
    lines = [data_line(chunk), ""]
    if case != "unterminated":
        lines += ["data: [DONE]", ""]
    http = AudioHTTP(response=FakeResponse(lines))
    runner, service, _, handle = setup_executor(approved_pair, http=http)
    with pytest.raises(CanonicalGenerationError) as caught:
        runner.transcribe(audio_clip(), release_after_generation=True)
    assert caught.value.category == ErrorCategory.PROTOCOL
    assert http.stream.closed is True
    assert_finished_once(service)
    assert len(handle.wait_calls) == 1


def test_real_stream_transport_failure_withholds_text_and_releases_once(approved_pair):
    def disconnect():
        raise requests.ConnectionError("private upstream transport details")

    http = AudioHTTP(response=FakeResponse(["data: [DONE]", ""], before_line=disconnect))
    runner, service, _, handle = setup_executor(approved_pair, http=http)
    with pytest.raises(CanonicalGenerationError) as caught:
        runner.transcribe(audio_clip(), release_after_generation=True)
    assert caught.value.category == ErrorCategory.TRANSPORT
    assert "private upstream" not in str(caught.value)
    assert http.stream.closed is True
    assert_finished_once(service)
    assert len(handle.wait_calls) == 1


def test_release_failure_withholds_otherwise_complete_transcription(approved_pair):
    runner, service, client, handle = setup_executor(approved_pair)
    handle.error = TimeoutError("terminal release did not complete")
    with pytest.raises(CanonicalGenerationError) as caught:
        runner.transcribe(audio_clip(), release_after_generation=True)
    assert caught.value.category == ErrorCategory.RELEASE_FAILED
    assert len(client.calls) == 1
    assert_finished_once(service)
    assert len(handle.wait_calls) == 1
    assert client.closed is True


def test_one_deadline_covers_preprocessing_identity_probe_stream_and_release(
    approved_pair, monkeypatch
):
    clock = Clock()
    prepare = execution_module.prepare_audio
    verify = execution_module.verify_approved_pair

    def slow_prepare(*args, **kwargs):
        clock.advance(2)
        return prepare(*args, **kwargs)

    def slow_verify(*args, **kwargs):
        clock.advance(3)
        return verify(*args, **kwargs)

    class TimedClient(FakeClient):
        def passive_props(self, *args, **kwargs):
            value = super().passive_props(*args, **kwargs)
            clock.advance(1)
            return value

        def stream_chat(self, *args, **kwargs):
            value = super().stream_chat(*args, **kwargs)
            clock.advance(1)
            return value

    monkeypatch.setattr(execution_module, "prepare_audio", slow_prepare)
    monkeypatch.setattr(execution_module, "verify_approved_pair", slow_verify)
    client = TimedClient(
        successful_stream("hello", thinking=""), props=SimpleNamespace(modalities={"audio": True})
    )
    runner, service, _, handle = setup_executor(approved_pair, client=client, clock=clock)
    *_, result, _ = runner.transcribe(
        audio_clip(), request_timeout=10, release_after_generation=True
    )
    assert client.props_calls[0]["timeout"] == 5
    assert client.calls[0]["timeout"] == 4
    assert client.calls[0]["chunk_timeout"] == 4
    assert handle.wait_calls == [3]
    assert result.timing.total_seconds == 7
    assert_finished_once(service)


@pytest.mark.parametrize("stage", ["preparation", "identity"])
def test_deadline_expiry_before_network_keeps_transcription_bounded(
    approved_pair, monkeypatch, stage
):
    clock = Clock()
    target = "prepare_audio" if stage == "preparation" else "verify_approved_pair"
    original = getattr(execution_module, target)

    def expire(*args, **kwargs):
        clock.advance(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(execution_module, target, expire)
    http = AudioHTTP()
    runner, service, _, handle = setup_executor(approved_pair, http=http, clock=clock)
    with pytest.raises(CanonicalGenerationError) as caught:
        runner.transcribe(audio_clip(), request_timeout=10, release_after_generation=True)
    assert caught.value.category == ErrorCategory.TIMEOUT
    assert http.calls == []
    if stage == "preparation":
        assert service.begin_calls == service.finish_calls == handle.wait_calls == []
    else:
        assert_finished_once(service)
        assert handle.wait_calls == [0]
