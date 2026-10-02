"""Synthetic waveform and file-identity tests; no real models or audio devices."""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import importlib
import io
import json
import os
import wave
from pathlib import Path

import numpy as np
import pytest

import generation.audio as audio_module
from generation.audio import (
    AUDIO_SAMPLE_RATE,
    MAX_AUDIO_SAMPLES,
    MAX_AUDIO_WAV_BYTES,
    SUPPORTED_ASR_PAIR,
    VERIFIED_ASR_PREFIX,
    ApprovedArtifact,
    AudioInputError,
    AudioPreparationCancelled,
    AudioResponseError,
    PreparedAudio,
    UnsupportedAudioModelError,
    parse_asr_response,
    prepare_audio,
    verify_approved_pair,
)


def clip(values=None):
    if values is None:
        values = np.array([-1.0, -0.5, 0.0, 0.5, 1.0], dtype=np.float32)
    return {"waveform": values.reshape(1, 1, -1), "sample_rate": AUDIO_SAMPLE_RATE}


def test_pcm_endpoints_wav_and_content_are_exact_without_mutating_input():
    source = clip()
    original = source["waveform"].copy()
    prepared = prepare_audio(source)
    with wave.open(io.BytesIO(prepared.wav_bytes), "rb") as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 16000)
        assert wav.getnframes() == 5
        assert np.frombuffer(wav.readframes(5), dtype="<i2").tolist() == [
            -32768,
            -16384,
            0,
            16384,
            32767,
        ]
    assert np.array_equal(source["waveform"], original)
    part = prepared.content_part()
    assert part["type"] == "input_audio"
    assert part["input_audio"]["format"] == "wav"
    assert base64.b64decode(part["input_audio"]["data"], validate=True) == prepared.wav_bytes
    assert prepared.wav_sha256 == hashlib.sha256(prepared.wav_bytes).hexdigest()
    assert prepared.duration_seconds == 5 / 16000
    assert "RIFF" not in repr(prepared)


def test_exact_ten_seconds_passes_and_one_more_sample_fails_before_copy():
    prepared = prepare_audio(clip(np.full(MAX_AUDIO_SAMPLES, 0.1, dtype=np.float32)))
    assert prepared.duration_seconds == 10
    assert len(prepared.wav_bytes) == MAX_AUDIO_WAV_BYTES

    class OversizedTensor:
        shape = (1, 1, MAX_AUDIO_SAMPLES + 1)

        def cpu(self):
            raise AssertionError("oversized tensor must not be copied")

    with pytest.raises(AudioInputError, match="160000"):
        prepare_audio({"waveform": OversizedTensor(), "sample_rate": 16000})


@pytest.mark.parametrize("shape", [(0, 1, 5), (2, 1, 5), (1, 2, 5), (5,), (1, 1, 0)])
def test_rejects_batch_stereo_missing_dimensions_and_empty(shape):
    with pytest.raises(AudioInputError):
        prepare_audio({"waveform": np.ones(shape, dtype=np.float32), "sample_rate": 16000})


@pytest.mark.parametrize("rate", [True, 0, -1, 8000, 44100, "16000", 16000.0, None])
def test_requires_exact_integer_16000_hz(rate):
    with pytest.raises(AudioInputError, match="16000"):
        prepare_audio({**clip(), "sample_rate": rate})


@pytest.mark.parametrize("value", [np.nan, np.inf, -np.inf, 1.001, -1.001])
def test_never_repairs_invalid_samples(value):
    with pytest.raises(AudioInputError, match="finite"):
        prepare_audio(clip(np.array([value], dtype=np.float32)))


@pytest.mark.parametrize("dtype", [np.int16, np.uint8, np.bool_, np.complex64, object])
def test_requires_native_floating_pcm(dtype):
    with pytest.raises(AudioInputError, match="floating"):
        prepare_audio(clip(np.ones(3, dtype=dtype)))


@pytest.mark.parametrize("value", [0.0, 1e-12, -1e-12])
def test_rejects_pcm16_silence_without_inventing_a_speech_threshold(value):
    with pytest.raises(AudioInputError, match="silent"):
        prepare_audio(clip(np.full(3, value, dtype=np.float32)))
    assert prepare_audio(clip(np.array([1 / 32767], dtype=np.float32))).samples == 1


@pytest.mark.parametrize("value", [None, [], {}, {"sample_rate": 16000}])
def test_rejects_non_audio_containers(value):
    with pytest.raises(AudioInputError):
        prepare_audio(value)


def test_tensor_protocol_and_control_checks():
    events = []

    class Tensor:
        shape = (1, 1, 5)

        def detach(self):
            events.append("detach")
            return self

        def cpu(self):
            events.append("cpu")
            return self

        def numpy(self):
            events.append("numpy")
            return clip()["waveform"]

    prepare_audio({"waveform": Tensor(), "sample_rate": 16000})
    assert events == ["detach", "cpu", "numpy"]
    with pytest.raises(AudioPreparationCancelled):
        prepare_audio(clip(), cancel_check=lambda: True)

    def expired():
        raise TimeoutError("deadline reached")

    with pytest.raises(TimeoutError):
        prepare_audio(clip(), deadline_check=expired)


def test_prepared_value_rejects_tampering():
    prepared = prepare_audio(clip())
    with pytest.raises(AudioInputError):
        dataclasses.replace(prepared, wav_sha256="0" * 64)
    with pytest.raises(AudioInputError):
        dataclasses.replace(prepared, samples=6)
    with pytest.raises(AudioInputError):
        PreparedAudio(b"x" * len(prepared.wav_bytes), prepared.samples, prepared.wav_sha256)


@pytest.fixture
def approved_files(tmp_path, monkeypatch):
    model = tmp_path / "renamed-model.gguf"
    projector = tmp_path / "renamed-projector.gguf"
    model.write_bytes(b"synthetic approved model")
    projector.write_bytes(b"synthetic approved projector")
    for name, path in (("APPROVED_MODEL", model), ("APPROVED_PROJECTOR", projector)):
        content = path.read_bytes()
        monkeypatch.setattr(
            audio_module, name, ApprovedArtifact(len(content), hashlib.sha256(content).hexdigest())
        )
    audio_module.clear_audio_identity_cache()
    yield model, projector
    audio_module.clear_audio_identity_cache()


def test_pair_verifies_content_not_names_and_metadata_has_no_payload_or_path(approved_files):
    identity = verify_approved_pair(*approved_files)
    prepared = prepare_audio(clip())
    operation = prepared.operation_metadata(identity)
    assert operation == {
        "kind": "transcribe",
        "version": 1,
        "format": "pcm16_wav",
        "sample_rate": 16000,
        "channels": 1,
        "samples": 5,
        "duration_seconds": 5 / 16000,
        "wav_sha256": prepared.wav_sha256,
        "model_sha256": audio_module.APPROVED_MODEL.sha256,
        "projector_sha256": audio_module.APPROVED_PROJECTOR.sha256,
    }
    metadata = parse_asr_response(VERIFIED_ASR_PREFIX + "Hello.").metadata_json(operation)
    assert json.loads(metadata)["language"] == "English"
    for forbidden in (str(approved_files[0]), "base64", "waveform", "input_audio"):
        assert forbidden not in metadata


def test_pair_rejects_wrong_variant_missing_and_changed_same_size_files(approved_files):
    model, projector = approved_files
    with pytest.raises(UnsupportedAudioModelError, match="supported"):
        verify_approved_pair(model, projector, supported_pair="another ASR model")
    with pytest.raises(UnsupportedAudioModelError, match="unavailable"):
        verify_approved_pair(model, None)
    verify_approved_pair(model, projector)
    prior = model.stat()
    model.write_bytes(b"x" * prior.st_size)
    os.utime(model, ns=(prior.st_atime_ns, prior.st_mtime_ns + 1_000_000_000))
    with pytest.raises(UnsupportedAudioModelError, match="SHA-256"):
        verify_approved_pair(model, projector)


def test_identity_cache_avoids_rehash_but_keeps_control_checks(approved_files, monkeypatch):
    verify_approved_pair(*approved_files)

    def no_open(*args, **kwargs):
        raise AssertionError("unchanged verified files should not be reread")

    monkeypatch.setattr(Path, "open", no_open)
    verify_approved_pair(*approved_files)
    with pytest.raises(AudioPreparationCancelled):
        verify_approved_pair(*approved_files, cancel_check=lambda: True)


def test_pair_rejects_model_changed_while_projector_is_read(approved_files, monkeypatch):
    model, projector = approved_files
    original_open = Path.open

    def change_model(path, *args, **kwargs):
        if path == projector:
            prior = model.stat()
            model.write_bytes(b"x" * prior.st_size)
            os.utime(model, ns=(prior.st_atime_ns, prior.st_mtime_ns + 1_000_000_000))
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", change_model)
    with pytest.raises(UnsupportedAudioModelError, match="pair changed"):
        verify_approved_pair(model, projector)


@pytest.mark.parametrize("cancel", [False, True])
def test_identity_read_obeys_deadline_and_cancellation_between_chunks(
    approved_files, monkeypatch, cancel
):
    monkeypatch.setattr(audio_module, "_HASH_CHUNK_BYTES", 2)
    calls = 0

    def check():
        nonlocal calls
        calls += 1
        if calls >= 3:
            if cancel:
                return True
            raise TimeoutError("deadline expired while hashing")
        return False

    options = {"cancel_check" if cancel else "deadline_check": check}
    with pytest.raises(AudioPreparationCancelled if cancel else TimeoutError):
        verify_approved_pair(*approved_files, **options)
    assert calls == 3


def test_verified_prefix_only_is_removed_and_raw_is_unchanged():
    raw = VERIFIED_ASR_PREFIX + "  The blue notebook is beside the window.\n"
    parsed = parse_asr_response(raw)
    assert parsed.raw == raw
    assert parsed.transcript == "  The blue notebook is beside the window.\n"
    assert parsed.language == "English"
    assert parsed.prefix_status == "verified_english"
    assert parsed.warnings == ()


@pytest.mark.parametrize(
    "raw",
    [
        "The blue notebook.",
        " language English<asr_text>Hello.",
        "language english<asr_text>Hello.",
        "language French<asr_text>Bonjour.",
        "Quoted <asr_text> marker.",
        "<asr_text>Hello.",
    ],
)
def test_unknown_prefix_variants_are_preserved_with_explicit_status(raw):
    parsed = parse_asr_response(raw)
    assert parsed.raw == parsed.transcript == raw
    assert parsed.language == ""
    assert parsed.prefix_status == "unrecognized_or_absent"
    assert parsed.warnings


@pytest.mark.parametrize("raw", [None, "", " \n", VERIFIED_ASR_PREFIX, VERIFIED_ASR_PREFIX + " \n"])
def test_empty_transcript_is_an_explicit_response_failure(raw):
    with pytest.raises(AudioResponseError):
        parse_asr_response(raw)


def test_transcribe_node_is_a_thin_coordinator_wrapper(node_package, monkeypatch):
    module = importlib.import_module(f"{node_package.__name__}.nodes.transcribe")
    sentinel_result = object()
    outputs = ("sentence", "raw sentence", "English", sentinel_result, '{"schema_version":1}')
    calls = []

    class Executor:
        def transcribe(self, audio, **kwargs):
            calls.append((audio, kwargs))
            return outputs

    monkeypatch.setattr(module, "CanonicalGenerationExecutor", Executor)
    monkeypatch.setattr(module, "_execution_identity", lambda *args: "execution-identity")
    audio = clip()
    result = module.LlamaCppTranscribe().transcribe(
        audio, server_url="http://127.0.0.1:8080", release_after_generation=True
    )
    assert result == {"ui": {"text": ("sentence",)}, "result": outputs}
    assert calls[0][0] is audio
    assert calls[0][1] == {
        "supported_pair": SUPPORTED_ASR_PAIR,
        "server_url": "http://127.0.0.1:8080",
        "request_timeout": 60,
        "release_after_generation": True,
        "identity": "execution-identity",
        "cancel_check": module._interrupt_check,
    }
    schema = module.LlamaCppTranscribe.INPUT_TYPES()
    assert schema["required"]["audio"][0] == "AUDIO"
    assert schema["required"]["supported_pair"][0] == [SUPPORTED_ASR_PAIR]
    assert (
        not {"model", "profile", "messages", "sampling_mode", "max_tokens"}
        & schema["required"].keys()
    )
