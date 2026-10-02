"""Bounded, in-memory preparation for the explicitly supported Qwen3-ASR pair.

This module performs no network, model loading, playback, or file creation.
Only the approved-pair verifier reads explicitly supplied model files.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import operator
import os
import stat
import threading
import wave
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

SUPPORTED_ASR_PAIR = "Qwen3-ASR 0.6B Q8_0 + matching Q8_0 audio projector"
AUDIO_SAMPLE_RATE = 16_000
MAX_AUDIO_SECONDS = 10
MAX_AUDIO_SAMPLES = AUDIO_SAMPLE_RATE * MAX_AUDIO_SECONDS
MAX_AUDIO_WAV_BYTES = 44 + MAX_AUDIO_SAMPLES * 2
ASR_MAX_TOKENS = 128
VERIFIED_ASR_PREFIX = "language English<asr_text>"
_HASH_CHUNK_BYTES = 1024 * 1024
_IDENTITY_CACHE_LIMIT = 8
_IDENTITY_CACHE: OrderedDict[tuple[Any, ...], None] = OrderedDict()
_IDENTITY_LOCK = threading.Lock()


class AudioInputError(ValueError):
    """A malformed or out-of-scope Comfy AUDIO value."""


class UnsupportedAudioModelError(ValueError):
    """The configured files are not the approved ASR pair."""


class AudioPreparationCancelled(InterruptedError):
    """Cancellation observed before submission or between identity reads."""


class AudioResponseError(ValueError):
    """A completed ASR response does not contain usable text."""


@dataclass(frozen=True, slots=True)
class ApprovedArtifact:
    size_bytes: int
    sha256: str


APPROVED_MODEL = ApprovedArtifact(
    804_749_248, "bca259818b50ca7c4c05e9bdb35a5dc04fa039653a6d6f3f0f331f96f6aa1971"
)
APPROVED_PROJECTOR = ApprovedArtifact(
    214_392_480, "41a342b5e4c514e968cb756de6cd1b7be39eff43c44c57a2ef5fc6522e36603d"
)


def _check_control(
    cancel_check: Callable[[], object] | None,
    deadline_check: Callable[[], object] | None = None,
) -> None:
    if cancel_check is not None and cancel_check():
        raise AudioPreparationCancelled("audio preparation was cancelled")
    if deadline_check is not None:
        deadline_check()


@dataclass(frozen=True, slots=True)
class ApprovedPairIdentity:
    model_sha256: str
    projector_sha256: str


@dataclass(frozen=True, slots=True)
class PreparedAudio:
    """Transient WAV bytes, never a workflow snapshot or serialized result."""

    wav_bytes: bytes = field(repr=False)
    samples: int
    wav_sha256: str

    def __post_init__(self) -> None:
        if type(self.samples) is not int or not 1 <= self.samples <= MAX_AUDIO_SAMPLES:
            raise AudioInputError("prepared audio has an invalid sample count")
        if type(self.wav_bytes) is not bytes or len(self.wav_bytes) != 44 + self.samples * 2:
            raise AudioInputError("prepared audio is not a bounded PCM16 WAV")
        try:
            with wave.open(io.BytesIO(self.wav_bytes), "rb") as source:
                valid = (
                    source.getnchannels() == 1
                    and source.getsampwidth() == 2
                    and source.getframerate() == AUDIO_SAMPLE_RATE
                    and source.getnframes() == self.samples
                    and source.getcomptype() == "NONE"
                )
        except (wave.Error, EOFError):
            valid = False
        if not valid or hashlib.sha256(self.wav_bytes).hexdigest() != self.wav_sha256:
            raise AudioInputError("prepared audio WAV structure or SHA-256 is invalid")

    @property
    def duration_seconds(self) -> float:
        return self.samples / AUDIO_SAMPLE_RATE

    def content_part(self) -> dict[str, Any]:
        return {
            "type": "input_audio",
            "input_audio": {
                "data": base64.b64encode(self.wav_bytes).decode("ascii"),
                "format": "wav",
            },
        }

    def operation_metadata(self, identity: ApprovedPairIdentity) -> dict[str, Any]:
        if not isinstance(identity, ApprovedPairIdentity) or (
            identity.model_sha256 != APPROVED_MODEL.sha256
            or identity.projector_sha256 != APPROVED_PROJECTOR.sha256
        ):
            raise UnsupportedAudioModelError("audio operation requires the approved ASR pair")
        return {
            "kind": "transcribe",
            "version": 1,
            "format": "pcm16_wav",
            "sample_rate": AUDIO_SAMPLE_RATE,
            "channels": 1,
            "samples": self.samples,
            "duration_seconds": self.duration_seconds,
            "wav_sha256": self.wav_sha256,
            "model_sha256": identity.model_sha256,
            "projector_sha256": identity.projector_sha256,
        }


def prepare_audio(
    audio: Any,
    *,
    cancel_check: Callable[[], object] | None = None,
    deadline_check: Callable[[], object] | None = None,
) -> PreparedAudio:
    """Accept one mono float PCM item at 16 kHz, with a strict ten-second bound.

    Reject before CPU conversion when tensor shape metadata is invalid.
    No clipping, downmixing, resampling, normalization, or silence threshold is
    hidden in this conversion. Exact PCM16 silence is rejected.
    """

    _check_control(cancel_check, deadline_check)
    if not isinstance(audio, Mapping) or "waveform" not in audio:
        raise AudioInputError("AUDIO must contain waveform and sample_rate")
    if type(audio.get("sample_rate")) is not int or audio["sample_rate"] != AUDIO_SAMPLE_RATE:
        raise AudioInputError("experimental transcription requires 16000 Hz audio")
    waveform = audio["waveform"]
    try:
        shape = tuple(operator.index(item) for item in waveform.shape)
    except (AttributeError, TypeError, ValueError, OverflowError):
        raise AudioInputError(
            "AUDIO waveform must be a tensor with shape [1, 1, samples]"
        ) from None
    if len(shape) != 3 or shape[:2] != (1, 1):
        raise AudioInputError("transcription accepts exactly one mono AUDIO item [1, 1, samples]")
    if not 1 <= shape[2] <= MAX_AUDIO_SAMPLES:
        raise AudioInputError(
            "audio must contain between 1 and 160000 samples (at most 10 seconds)"
        )
    try:
        if hasattr(waveform, "detach"):
            waveform = waveform.detach()
        if hasattr(waveform, "cpu"):
            waveform = waveform.cpu()
        if hasattr(waveform, "numpy"):
            waveform = waveform.numpy()
        values = np.asarray(waveform)
    except Exception as exc:
        raise AudioInputError(f"could not read AUDIO waveform ({type(exc).__name__})") from None
    _check_control(cancel_check, deadline_check)
    if values.shape != shape or not np.issubdtype(values.dtype, np.floating):
        raise AudioInputError("AUDIO waveform must contain floating-point PCM samples")
    if not np.isfinite(values).all() or np.any(values < -1.0) or np.any(values > 1.0):
        raise AudioInputError("AUDIO PCM samples must be finite and within [-1, 1]")
    # Asymmetric PCM16 endpoints: -1 -> -32768, +1 -> +32767.
    normalized = values.reshape(-1).astype(np.float64)
    pcm = np.rint(np.where(normalized < 0, normalized * 32768, normalized * 32767))
    pcm = pcm.astype("<i2")
    if not np.any(pcm):
        raise AudioInputError("audio is silent after PCM16 conversion")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(AUDIO_SAMPLE_RATE)
        output.writeframes(pcm.tobytes())
    wav_bytes = buffer.getvalue()
    if len(wav_bytes) > MAX_AUDIO_WAV_BYTES:  # defensive encoder invariant
        raise AudioInputError("encoded audio exceeds the WAV byte limit")
    _check_control(cancel_check, deadline_check)
    return PreparedAudio(wav_bytes, shape[2], hashlib.sha256(wav_bytes).hexdigest())


def _signature(info: os.stat_result, *, cross_platform: bool = False) -> tuple[int, ...]:
    common = (int(info.st_dev), int(info.st_ino), int(info.st_size), int(info.st_mtime_ns))
    return common if cross_platform else (*common, int(info.st_ctime_ns))


def clear_audio_identity_cache() -> None:
    with _IDENTITY_LOCK:
        _IDENTITY_CACHE.clear()


def _verify_artifact(
    path: str | os.PathLike[str],
    expected: ApprovedArtifact,
    *,
    cancel_check: Callable[[], object] | None,
    deadline_check: Callable[[], object] | None,
) -> tuple[Path, tuple[int, ...]]:
    _check_control(cancel_check, deadline_check)
    try:
        resolved = Path(path).resolve(strict=True)
        before = resolved.stat()
        if not stat.S_ISREG(before.st_mode) or before.st_size != expected.size_bytes:
            raise UnsupportedAudioModelError("configured ASR artifact has an unsupported size")
        signature = _signature(before)
        key = (os.path.normcase(str(resolved)), signature, expected.sha256)
        with _IDENTITY_LOCK:
            if key in _IDENTITY_CACHE:
                _IDENTITY_CACHE.move_to_end(key)
                return resolved, signature
        digest = hashlib.sha256()
        with resolved.open("rb") as source:
            opened = os.fstat(source.fileno())
            if _signature(before, cross_platform=True) != _signature(opened, cross_platform=True):
                raise UnsupportedAudioModelError(
                    "configured ASR artifact changed before inspection"
                )
            remaining = expected.size_bytes
            while remaining:
                _check_control(cancel_check, deadline_check)
                chunk = source.read(min(_HASH_CHUNK_BYTES, remaining))
                if not chunk:
                    raise UnsupportedAudioModelError(
                        "configured ASR artifact changed during inspection"
                    )
                digest.update(chunk)
                remaining -= len(chunk)
            if source.read(1) or _signature(opened) != _signature(os.fstat(source.fileno())):
                raise UnsupportedAudioModelError(
                    "configured ASR artifact changed during inspection"
                )
        if signature != _signature(resolved.stat()):
            raise UnsupportedAudioModelError("configured ASR artifact changed during inspection")
    except (AudioPreparationCancelled, TimeoutError):
        raise
    except (OSError, TypeError, ValueError) as exc:
        if isinstance(exc, UnsupportedAudioModelError):
            raise
        raise UnsupportedAudioModelError("configured ASR artifact is unavailable") from None
    _check_control(cancel_check, deadline_check)
    if digest.hexdigest() != expected.sha256:
        raise UnsupportedAudioModelError(
            "configured ASR artifact does not match the approved SHA-256"
        )
    with _IDENTITY_LOCK:
        _IDENTITY_CACHE[key] = None
        _IDENTITY_CACHE.move_to_end(key)
        while len(_IDENTITY_CACHE) > _IDENTITY_CACHE_LIMIT:
            _IDENTITY_CACHE.popitem(last=False)
    return resolved, signature


def verify_approved_pair(
    model_path: str | os.PathLike[str],
    projector_path: str | os.PathLike[str],
    *,
    supported_pair: str = SUPPORTED_ASR_PAIR,
    cancel_check: Callable[[], object] | None = None,
    deadline_check: Callable[[], object] | None = None,
) -> ApprovedPairIdentity:
    """Verify configured local files, not filenames or the shared qwen3vl architecture.

    The coordinator must separately prove managed direct ownership, retain its
    admitted runtime epoch, and require passive modalities.audio is exactly True.
    """

    if supported_pair != SUPPORTED_ASR_PAIR:
        raise UnsupportedAudioModelError("select the supported Qwen3-ASR 0.6B Q8_0 pair")
    inspected = []
    for path, expected in ((model_path, APPROVED_MODEL), (projector_path, APPROVED_PROJECTOR)):
        inspected.append(
            _verify_artifact(
                path, expected, cancel_check=cancel_check, deadline_check=deadline_check
            )
        )
    _check_control(cancel_check, deadline_check)
    for path, signature in inspected:
        try:
            unchanged = _signature(path.stat()) == signature
        except OSError:
            unchanged = False
        if not unchanged:
            raise UnsupportedAudioModelError("configured ASR pair changed during inspection")
    return ApprovedPairIdentity(APPROVED_MODEL.sha256, APPROVED_PROJECTOR.sha256)


@dataclass(frozen=True, slots=True)
class ParsedTranscript:
    transcript: str
    raw: str
    language: str
    prefix_status: str
    warnings: tuple[str, ...] = ()

    def metadata_json(self, operation: Mapping[str, Any]) -> str:
        return json.dumps(
            {
                "schema_version": 1,
                "operation": dict(operation),
                "language": self.language,
                "prefix_status": self.prefix_status,
                "warnings": list(self.warnings),
            },
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )


def parse_asr_response(raw: str) -> ParsedTranscript:
    """Remove only the exact observed leading prefix; preserve all other text."""

    if not isinstance(raw, str) or not raw.strip():
        raise AudioResponseError("ASR returned no transcript text")
    if raw.startswith(VERIFIED_ASR_PREFIX):
        transcript = raw[len(VERIFIED_ASR_PREFIX) :]
        if not transcript.strip():
            raise AudioResponseError("ASR returned its prefix without transcript text")
        return ParsedTranscript(transcript, raw, "English", "verified_english")
    return ParsedTranscript(
        raw,
        raw,
        "",
        "unrecognized_or_absent",
        ("ASR prefix was not recognized; transcript preserves the complete raw response",),
    )
