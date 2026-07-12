"""Versioned, JSON-safe contracts for canonical llama.cpp generation.

These value objects intentionally contain only workflow and diagnostic metadata.
Encoded image payloads, resolved API keys, arbitrary server bodies, live event
state, and process objects belong to the execution layer and must never be
retained here.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, ClassVar, TypeVar
from urllib.parse import urlsplit

SCHEMA_VERSION = 1
MAX_CONNECTION_SNAPSHOT_BYTES = 65_536
MAX_REQUEST_JSON_BYTES = 16_777_216
MAX_RESULT_JSON_BYTES = 67_108_864
MAX_REQUEST_TEXT_CHARS = 1_048_576
MAX_RESULT_TEXT_CHARS = 8_388_608
MAX_MODEL_TEXT_CHARS = 4_096
MAX_STOP_SEQUENCES = 128
MAX_STOP_SEQUENCE_CHARS = 4_096
MAX_REQUEST_IMAGE_COUNT = 4_096
MAX_TOKEN_BIAS_COUNT = 4_096
MAX_TOKEN_BIAS_JSON_BYTES = 1_048_576
MAX_ERROR_MESSAGE_CHARS = 4_096
MAX_WARNING_COUNT = 64
MAX_WARNING_CHARS = 4_096

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_API_KEY_ENV_RE = re.compile(r"^LLAMACPP_API_KEY(?:_[A-Z0-9_]+)?$")
_CONNECTION_KEYS = frozenset(
    {
        "schema_version",
        "server_url",
        "model",
        "api_key_env",
        "verify_tls",
        "request_timeout",
    }
)
_STRUCTURED_OUTPUT_KINDS = frozenset({"json_schema", "json_object", "grammar"})


class ThinkingMode(str, Enum):
    AUTO = "auto"
    OFF = "off"
    ON = "on"


class SamplingMode(str, Enum):
    DEFAULT = "default"
    CUSTOM = "custom"


class PartialOutputPolicy(str, Enum):
    RAISE_ERROR = "raise_error"
    RETURN_MARKED_PARTIAL = "return_marked_partial"


class ReleasePolicy(str, Enum):
    REUSE = "reuse"
    RELEASE_AFTER_GENERATION = "release_after_generation"


class GenerationState(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    CANCELLED = "cancelled"


class ErrorCategory(str, Enum):
    INVALID_REQUEST = "invalid_request"
    RUNTIME_UNAVAILABLE = "runtime_unavailable"
    MODEL_MISSING = "model_missing"
    CAPABILITY_UNSUPPORTED = "capability_unsupported"
    AUTHENTICATION = "authentication"
    TLS = "tls"
    TIMEOUT = "timeout"
    TRANSPORT = "transport"
    SERVER = "server"
    PROTOCOL = "protocol"
    STRUCTURED_JSON = "structured_json"
    CANCELLED = "cancelled"
    RELEASE_FAILED = "release_failed"


EnumType = TypeVar("EnumType", bound=Enum)


def _enum_value(enum_type: type[EnumType], value: EnumType | str, field_name: str) -> EnumType:
    if isinstance(value, enum_type):
        return value
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string or {enum_type.__name__}")
    try:
        return enum_type(value)
    except ValueError as exc:
        choices = ", ".join(item.value for item in enum_type)
        raise ValueError(f"{field_name} must be one of: {choices}") from exc


def _exact_keys(value: Mapping[str, Any], expected: set[str] | frozenset[str], name: str) -> None:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be an object")
    actual = set(value)
    if actual == set(expected):
        return
    missing = sorted(set(expected) - actual, key=repr)
    extra = sorted(actual - set(expected), key=repr)
    details = []
    if missing:
        details.append(f"missing {missing}")
    if extra:
        details.append(f"unexpected {extra}")
    raise ValueError(f"{name} has invalid keys ({'; '.join(details)})")


def _schema_version(value: Any, name: str, expected: int = SCHEMA_VERSION) -> int:
    version = _strict_int(value, f"{name} schema_version", maximum=expected)
    if version != expected:
        raise ValueError(f"{name} schema_version must be {expected}")
    return version


def _bounded_string(
    value: Any,
    field_name: str,
    maximum: int,
    *,
    allow_empty: bool = True,
) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not allow_empty and not value:
        raise ValueError(f"{field_name} must not be empty")
    if len(value) > maximum:
        raise ValueError(f"{field_name} exceeds {maximum} characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field_name} must be valid UTF-8 text") from exc
    return value


def _optional_string(value: Any, field_name: str, maximum: int) -> str | None:
    if value is None:
        return None
    return _bounded_string(value, field_name, maximum)


def _strict_bool(value: Any, field_name: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{field_name} must be a Boolean")
    return value


def _strict_int(value: Any, field_name: str, *, minimum: int = 0, maximum: int) -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{field_name} must be between {minimum} and {maximum}")
    return value


def _optional_nonnegative_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    return _strict_int(value, field_name, maximum=2**63 - 1)


def _optional_duration(value: Any, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a number or null")
    duration = float(value)
    if not math.isfinite(duration) or duration < 0:
        raise ValueError(f"{field_name} must be a finite non-negative number")
    return duration


def _compact_json(
    value: Mapping[str, Any],
    *,
    maximum_bytes: int | None = None,
    name: str = "JSON contract",
) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if maximum_bytes is not None and len(encoded.encode("utf-8")) > maximum_bytes:
        raise ValueError(f"{name} exceeds {maximum_bytes} bytes")
    return encoded


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _json_object(value: str | bytes, name: str, *, maximum_bytes: int) -> Mapping[str, Any]:
    if isinstance(value, bytes):
        encoded = value
        try:
            text = encoded.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"{name} is not valid UTF-8 JSON: {exc}") from exc
    elif isinstance(value, str):
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError(f"{name} is not valid UTF-8 JSON: {exc}") from exc
        text = value
    else:
        raise TypeError(f"{name} must be JSON text")
    if len(encoded) > maximum_bytes:
        raise ValueError(f"{name} exceeds {maximum_bytes} bytes")
    if text.startswith("\ufeff"):
        raise ValueError(f"{name} must not contain a UTF-8 BOM")
    try:
        parsed = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{name} is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(parsed, Mapping):
        raise ValueError(f"{name} must contain a JSON object")
    return parsed


def _normalize_connection(value: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("connection must be a mapping")
    _exact_keys(value, _CONNECTION_KEYS, "connection")
    version = _strict_int(value["schema_version"], "connection.schema_version", maximum=1)
    if version != SCHEMA_VERSION:
        raise ValueError(f"connection.schema_version must be {SCHEMA_VERSION}")
    server_url = _bounded_string(value["server_url"], "connection.server_url", 8_192)
    if server_url:
        try:
            parsed = urlsplit(server_url)
            _port = parsed.port
        except ValueError as exc:
            raise ValueError(f"connection.server_url is invalid: {exc}") from exc
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("connection.server_url must be an absolute HTTP or HTTPS URL")
        if parsed.username or parsed.password:
            raise ValueError("connection.server_url must not contain credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("connection.server_url must not contain a query or fragment")
    api_key_env = _bounded_string(value["api_key_env"], "connection.api_key_env", 256)
    if api_key_env and not _API_KEY_ENV_RE.fullmatch(api_key_env):
        raise ValueError(
            "connection.api_key_env must be LLAMACPP_API_KEY or LLAMACPP_API_KEY_<UPPERCASE_SUFFIX>"
        )
    normalized = {
        "schema_version": version,
        "server_url": server_url,
        "model": _bounded_string(value["model"], "connection.model", MAX_MODEL_TEXT_CHARS),
        "api_key_env": api_key_env,
        "verify_tls": _strict_bool(value["verify_tls"], "connection.verify_tls"),
        "request_timeout": _strict_int(
            value["request_timeout"],
            "connection.request_timeout",
            minimum=1,
            maximum=86_400,
        ),
    }
    _compact_json(
        normalized,
        maximum_bytes=MAX_CONNECTION_SNAPSHOT_BYTES,
        name="connection snapshot",
    )
    return MappingProxyType(normalized)


@dataclass(frozen=True, slots=True)
class SamplingSettings:
    mode: SamplingMode = SamplingMode.DEFAULT
    temperature: float = 0.7
    top_p: float = 0.9
    top_k: int = 40
    min_p: float = 0.05
    repeat_penalty: float = 1.1
    presence_penalty: float = 0.0
    frequency_penalty: float = 0.0

    SCHEMA_VERSION: ClassVar[int] = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", _enum_value(SamplingMode, self.mode, "mode"))
        for field_name in (
            "temperature",
            "top_p",
            "min_p",
            "repeat_penalty",
            "presence_penalty",
            "frequency_penalty",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{field_name} must be a number")
            if not math.isfinite(float(value)):
                raise ValueError(f"{field_name} must be finite")
            object.__setattr__(self, field_name, float(value))
        _strict_int(self.top_k, "top_k", maximum=1_000_000)
        if not 0.0 <= self.temperature <= 2.0:
            raise ValueError("temperature must be between 0 and 2")
        if not 0.0 <= self.top_p <= 1.0:
            raise ValueError("top_p must be between 0 and 1")
        if not 0.0 <= self.min_p <= 1.0:
            raise ValueError("min_p must be between 0 and 1")
        if not 1.0 <= self.repeat_penalty <= 2.0:
            raise ValueError("repeat_penalty must be between 1 and 2")
        if not -2.0 <= self.presence_penalty <= 2.0:
            raise ValueError("presence_penalty must be between -2 and 2")
        if not -2.0 <= self.frequency_penalty <= 2.0:
            raise ValueError("frequency_penalty must be between -2 and 2")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "mode": self.mode.value,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "top_k": self.top_k,
            "min_p": self.min_p,
            "repeat_penalty": self.repeat_penalty,
            "presence_penalty": self.presence_penalty,
            "frequency_penalty": self.frequency_penalty,
        }

    def to_json(self) -> str:
        return _compact_json(
            self.as_dict(),
            maximum_bytes=65_536,
            name="sampling settings",
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> SamplingSettings:
        expected = {
            "schema_version",
            "mode",
            "temperature",
            "top_p",
            "top_k",
            "min_p",
            "repeat_penalty",
            "presence_penalty",
            "frequency_penalty",
        }
        _exact_keys(value, expected, "sampling settings")
        _schema_version(value["schema_version"], "sampling settings", cls.SCHEMA_VERSION)
        return cls(
            mode=value["mode"],
            temperature=value["temperature"],
            top_p=value["top_p"],
            top_k=value["top_k"],
            min_p=value["min_p"],
            repeat_penalty=value["repeat_penalty"],
            presence_penalty=value["presence_penalty"],
            frequency_penalty=value["frequency_penalty"],
        )

    @classmethod
    def from_json(cls, value: str | bytes) -> SamplingSettings:
        return cls.from_dict(_json_object(value, "sampling settings", maximum_bytes=65_536))


@dataclass(frozen=True, slots=True)
class GenerationRequestSpec:
    connection: Mapping[str, Any]
    prompt: str
    system_prompt: str = ""
    requested_model: str | None = None
    profile_id: str = "freeform"
    profile_sha256: str = ""
    thinking_mode: ThinkingMode = ThinkingMode.AUTO
    sampling: SamplingSettings = field(default_factory=SamplingSettings)
    max_tokens: int = 2_048
    seed: int = 0
    cache_prompt: bool = False
    stop_sequences: tuple[str, ...] = ()
    image_count: int = 0
    structured_output_kind: str | None = None
    token_bias_count: int = 0
    release_policy: ReleasePolicy = ReleasePolicy.REUSE
    partial_output_policy: PartialOutputPolicy = PartialOutputPolicy.RAISE_ERROR

    SCHEMA_VERSION: ClassVar[int] = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "connection", _normalize_connection(self.connection))
        object.__setattr__(
            self,
            "prompt",
            _bounded_string(self.prompt, "prompt", MAX_REQUEST_TEXT_CHARS),
        )
        if not self.prompt.strip():
            raise ValueError("prompt must not be empty")
        object.__setattr__(
            self,
            "system_prompt",
            _bounded_string(self.system_prompt, "system_prompt", MAX_REQUEST_TEXT_CHARS),
        )
        object.__setattr__(
            self,
            "requested_model",
            _optional_string(self.requested_model, "requested_model", MAX_MODEL_TEXT_CHARS),
        )
        object.__setattr__(
            self,
            "profile_id",
            _bounded_string(self.profile_id, "profile_id", 64, allow_empty=False),
        )
        if not _SHA256_RE.fullmatch(self.profile_sha256):
            raise ValueError("profile_sha256 must be a lowercase SHA-256 hex digest")
        object.__setattr__(
            self,
            "thinking_mode",
            _enum_value(ThinkingMode, self.thinking_mode, "thinking_mode"),
        )
        if not isinstance(self.sampling, SamplingSettings):
            raise TypeError("sampling must be SamplingSettings")
        _strict_int(self.max_tokens, "max_tokens", minimum=1, maximum=1_048_576)
        _strict_int(self.seed, "seed", maximum=0x7FFFFFFF)
        _strict_bool(self.cache_prompt, "cache_prompt")
        if isinstance(self.stop_sequences, str) or not isinstance(self.stop_sequences, Sequence):
            raise TypeError("stop_sequences must be a sequence of strings")
        stops = tuple(
            _bounded_string(item, f"stop_sequences[{index}]", MAX_STOP_SEQUENCE_CHARS)
            for index, item in enumerate(self.stop_sequences)
        )
        if len(stops) > MAX_STOP_SEQUENCES:
            raise ValueError(f"stop_sequences exceeds {MAX_STOP_SEQUENCES} entries")
        object.__setattr__(self, "stop_sequences", stops)
        _strict_int(self.image_count, "image_count", maximum=MAX_REQUEST_IMAGE_COUNT)
        structured_kind = _optional_string(
            self.structured_output_kind,
            "structured_output_kind",
            64,
        )
        if structured_kind is not None and structured_kind not in _STRUCTURED_OUTPUT_KINDS:
            choices = ", ".join(sorted(_STRUCTURED_OUTPUT_KINDS))
            raise ValueError(f"structured_output_kind must be one of: {choices}")
        object.__setattr__(self, "structured_output_kind", structured_kind)
        _strict_int(
            self.token_bias_count,
            "token_bias_count",
            maximum=MAX_TOKEN_BIAS_COUNT,
        )
        object.__setattr__(
            self,
            "release_policy",
            _enum_value(ReleasePolicy, self.release_policy, "release_policy"),
        )
        object.__setattr__(
            self,
            "partial_output_policy",
            _enum_value(
                PartialOutputPolicy,
                self.partial_output_policy,
                "partial_output_policy",
            ),
        )
        _compact_json(
            self.as_dict(),
            maximum_bytes=MAX_REQUEST_JSON_BYTES,
            name="generation request",
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "connection": dict(self.connection),
            "prompt": self.prompt,
            "system_prompt": self.system_prompt,
            "requested_model": self.requested_model,
            "profile_id": self.profile_id,
            "profile_sha256": self.profile_sha256,
            "thinking_mode": self.thinking_mode.value,
            "sampling": self.sampling.as_dict(),
            "max_tokens": self.max_tokens,
            "seed": self.seed,
            "cache_prompt": self.cache_prompt,
            "stop_sequences": list(self.stop_sequences),
            "image_count": self.image_count,
            "structured_output_kind": self.structured_output_kind,
            "token_bias_count": self.token_bias_count,
            "release_policy": self.release_policy.value,
            "partial_output_policy": self.partial_output_policy.value,
        }

    def to_json(self) -> str:
        return _compact_json(
            self.as_dict(),
            maximum_bytes=MAX_REQUEST_JSON_BYTES,
            name="generation request",
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> GenerationRequestSpec:
        expected = {
            "schema_version",
            "connection",
            "prompt",
            "system_prompt",
            "requested_model",
            "profile_id",
            "profile_sha256",
            "thinking_mode",
            "sampling",
            "max_tokens",
            "seed",
            "cache_prompt",
            "stop_sequences",
            "image_count",
            "structured_output_kind",
            "token_bias_count",
            "release_policy",
            "partial_output_policy",
        }
        _exact_keys(value, expected, "generation request")
        _schema_version(value["schema_version"], "generation request", cls.SCHEMA_VERSION)
        sampling = value["sampling"]
        if not isinstance(sampling, Mapping):
            raise TypeError("generation request sampling must be an object")
        connection = value["connection"]
        if not isinstance(connection, Mapping):
            raise TypeError("generation request connection must be an object")
        return cls(
            connection=connection,
            prompt=value["prompt"],
            system_prompt=value["system_prompt"],
            requested_model=value["requested_model"],
            profile_id=value["profile_id"],
            profile_sha256=value["profile_sha256"],
            thinking_mode=value["thinking_mode"],
            sampling=SamplingSettings.from_dict(sampling),
            max_tokens=value["max_tokens"],
            seed=value["seed"],
            cache_prompt=value["cache_prompt"],
            stop_sequences=value["stop_sequences"],
            image_count=value["image_count"],
            structured_output_kind=value["structured_output_kind"],
            token_bias_count=value["token_bias_count"],
            release_policy=value["release_policy"],
            partial_output_policy=value["partial_output_policy"],
        )

    @classmethod
    def from_json(cls, value: str | bytes) -> GenerationRequestSpec:
        return cls.from_dict(
            _json_object(value, "generation request", maximum_bytes=MAX_REQUEST_JSON_BYTES)
        )


@dataclass(frozen=True, slots=True)
class GenerationUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None

    SCHEMA_VERSION: ClassVar[int] = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name in ("prompt_tokens", "completion_tokens", "total_tokens"):
            object.__setattr__(
                self,
                field_name,
                _optional_nonnegative_int(getattr(self, field_name), field_name),
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> GenerationUsage:
        expected = {"schema_version", "prompt_tokens", "completion_tokens", "total_tokens"}
        _exact_keys(value, expected, "generation usage")
        _schema_version(value["schema_version"], "generation usage", cls.SCHEMA_VERSION)
        return cls(
            prompt_tokens=value["prompt_tokens"],
            completion_tokens=value["completion_tokens"],
            total_tokens=value["total_tokens"],
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> GenerationUsage:
        if not value:
            return cls()
        return cls(
            prompt_tokens=value.get("prompt_tokens"),
            completion_tokens=value.get("completion_tokens"),
            total_tokens=value.get("total_tokens"),
        )


@dataclass(frozen=True, slots=True)
class GenerationTiming:
    first_chunk_seconds: float | None = None
    generation_seconds: float | None = None
    release_seconds: float | None = None
    total_seconds: float | None = None

    SCHEMA_VERSION: ClassVar[int] = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name in (
            "first_chunk_seconds",
            "generation_seconds",
            "release_seconds",
            "total_seconds",
        ):
            object.__setattr__(
                self,
                field_name,
                _optional_duration(getattr(self, field_name), field_name),
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "first_chunk_seconds": self.first_chunk_seconds,
            "generation_seconds": self.generation_seconds,
            "release_seconds": self.release_seconds,
            "total_seconds": self.total_seconds,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> GenerationTiming:
        expected = {
            "schema_version",
            "first_chunk_seconds",
            "generation_seconds",
            "release_seconds",
            "total_seconds",
        }
        _exact_keys(value, expected, "generation timing")
        _schema_version(value["schema_version"], "generation timing", cls.SCHEMA_VERSION)
        return cls(
            first_chunk_seconds=value["first_chunk_seconds"],
            generation_seconds=value["generation_seconds"],
            release_seconds=value["release_seconds"],
            total_seconds=value["total_seconds"],
        )


@dataclass(frozen=True, slots=True)
class GenerationErrorInfo:
    category: ErrorCategory
    message: str
    status_code: int | None = None
    retryable: bool = False

    SCHEMA_VERSION: ClassVar[int] = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "category",
            _enum_value(ErrorCategory, self.category, "category"),
        )
        object.__setattr__(
            self,
            "message",
            _bounded_string(
                self.message,
                "message",
                MAX_ERROR_MESSAGE_CHARS,
                allow_empty=False,
            ),
        )
        if self.status_code is not None:
            _strict_int(self.status_code, "status_code", minimum=100, maximum=599)
        _strict_bool(self.retryable, "retryable")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "category": self.category.value,
            "message": self.message,
            "status_code": self.status_code,
            "retryable": self.retryable,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> GenerationErrorInfo:
        expected = {"schema_version", "category", "message", "status_code", "retryable"}
        _exact_keys(value, expected, "generation error")
        _schema_version(value["schema_version"], "generation error", cls.SCHEMA_VERSION)
        return cls(
            category=value["category"],
            message=value["message"],
            status_code=value["status_code"],
            retryable=value["retryable"],
        )


@dataclass(frozen=True, slots=True)
class GenerationReleaseInfo:
    policy: ReleasePolicy = ReleasePolicy.REUSE
    status: str = "not_requested"
    mode: str | None = None
    target_model: str | None = None
    request_id: str | None = None
    operation_id: str | None = None
    scope: str | None = None
    coalesced: bool = False
    superseded_by: str | None = None
    driver_memory_verified: bool = False
    terminal: bool = True
    success: bool = True
    elapsed_seconds: float | None = None
    released_models: tuple[str, ...] = ()
    error: GenerationErrorInfo | None = None

    SCHEMA_VERSION: ClassVar[int] = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy", _enum_value(ReleasePolicy, self.policy, "policy"))
        status_value = self.status.value if isinstance(self.status, Enum) else self.status
        object.__setattr__(
            self,
            "status",
            _bounded_string(status_value, "status", 64, allow_empty=False),
        )
        object.__setattr__(self, "mode", _optional_string(self.mode, "mode", 64))
        object.__setattr__(
            self,
            "target_model",
            _optional_string(self.target_model, "target_model", MAX_MODEL_TEXT_CHARS),
        )
        for field_name in ("request_id", "operation_id", "superseded_by"):
            object.__setattr__(
                self,
                field_name,
                _optional_string(getattr(self, field_name), field_name, 128),
            )
        object.__setattr__(self, "scope", _optional_string(self.scope, "scope", 64))
        _strict_bool(self.coalesced, "coalesced")
        _strict_bool(self.driver_memory_verified, "driver_memory_verified")
        if self.driver_memory_verified:
            raise ValueError(
                "driver_memory_verified must remain false without independent device proof"
            )
        _strict_bool(self.terminal, "terminal")
        _strict_bool(self.success, "success")
        object.__setattr__(
            self,
            "elapsed_seconds",
            _optional_duration(self.elapsed_seconds, "elapsed_seconds"),
        )
        if isinstance(self.released_models, str) or not isinstance(self.released_models, Sequence):
            raise TypeError("released_models must be a sequence of model IDs")
        released_models = tuple(
            _bounded_string(item, f"released_models[{index}]", MAX_MODEL_TEXT_CHARS)
            for index, item in enumerate(self.released_models)
        )
        if len(released_models) > 128:
            raise ValueError("released_models exceeds 128 entries")
        object.__setattr__(self, "released_models", released_models)
        if self.error is not None and not isinstance(self.error, GenerationErrorInfo):
            raise TypeError("error must be GenerationErrorInfo or null")
        if self.success and self.error is not None:
            raise ValueError("a successful release cannot contain an error")
        if self.success and not self.terminal:
            raise ValueError("a successful release must be terminal")
        if not self.success and self.terminal and self.error is None:
            raise ValueError("a failed release must contain an error")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "policy": self.policy.value,
            "status": self.status,
            "mode": self.mode,
            "target_model": self.target_model,
            "request_id": self.request_id,
            "operation_id": self.operation_id,
            "scope": self.scope,
            "coalesced": self.coalesced,
            "superseded_by": self.superseded_by,
            "driver_memory_verified": self.driver_memory_verified,
            "terminal": self.terminal,
            "success": self.success,
            "elapsed_seconds": self.elapsed_seconds,
            "released_models": list(self.released_models),
            "error": self.error.as_dict() if self.error else None,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> GenerationReleaseInfo:
        expected = {
            "schema_version",
            "policy",
            "status",
            "mode",
            "target_model",
            "request_id",
            "operation_id",
            "scope",
            "coalesced",
            "superseded_by",
            "driver_memory_verified",
            "terminal",
            "success",
            "elapsed_seconds",
            "released_models",
            "error",
        }
        _exact_keys(value, expected, "generation release")
        _schema_version(value["schema_version"], "generation release", cls.SCHEMA_VERSION)
        error_value = value["error"]
        if error_value is not None and not isinstance(error_value, Mapping):
            raise TypeError("generation release error must be an object or null")
        return cls(
            policy=value["policy"],
            status=value["status"],
            mode=value["mode"],
            target_model=value["target_model"],
            request_id=value["request_id"],
            operation_id=value["operation_id"],
            scope=value["scope"],
            coalesced=value["coalesced"],
            superseded_by=value["superseded_by"],
            driver_memory_verified=value["driver_memory_verified"],
            terminal=value["terminal"],
            success=value["success"],
            elapsed_seconds=value["elapsed_seconds"],
            released_models=value["released_models"],
            error=GenerationErrorInfo.from_dict(error_value) if error_value else None,
        )


@dataclass(frozen=True, slots=True)
class GenerationResult:
    state: GenerationState
    response: str
    thinking: str
    requested_model: str | None
    effective_model: str | None
    profile_id: str
    profile_sha256: str
    seed: int
    image_count: int
    structured_output_kind: str | None
    usage: GenerationUsage = field(default_factory=GenerationUsage)
    finish_reason: str | None = None
    response_id: str | None = None
    chunks: int = 0
    terminal: bool = False
    done_received: bool = False
    timing: GenerationTiming = field(default_factory=GenerationTiming)
    release: GenerationReleaseInfo = field(default_factory=GenerationReleaseInfo)
    error: GenerationErrorInfo | None = None
    warnings: tuple[str, ...] = ()

    SCHEMA_VERSION: ClassVar[int] = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", _enum_value(GenerationState, self.state, "state"))
        object.__setattr__(
            self,
            "response",
            _bounded_string(self.response, "response", MAX_RESULT_TEXT_CHARS),
        )
        object.__setattr__(
            self,
            "thinking",
            _bounded_string(self.thinking, "thinking", MAX_RESULT_TEXT_CHARS),
        )
        object.__setattr__(
            self,
            "requested_model",
            _optional_string(self.requested_model, "requested_model", MAX_MODEL_TEXT_CHARS),
        )
        object.__setattr__(
            self,
            "effective_model",
            _optional_string(self.effective_model, "effective_model", MAX_MODEL_TEXT_CHARS),
        )
        object.__setattr__(
            self,
            "profile_id",
            _bounded_string(self.profile_id, "profile_id", 64, allow_empty=False),
        )
        if not _SHA256_RE.fullmatch(self.profile_sha256):
            raise ValueError("profile_sha256 must be a lowercase SHA-256 hex digest")
        _strict_int(self.seed, "seed", maximum=0x7FFFFFFF)
        _strict_int(self.image_count, "image_count", maximum=MAX_REQUEST_IMAGE_COUNT)
        structured_kind = _optional_string(
            self.structured_output_kind,
            "structured_output_kind",
            64,
        )
        if structured_kind is not None and structured_kind not in _STRUCTURED_OUTPUT_KINDS:
            choices = ", ".join(sorted(_STRUCTURED_OUTPUT_KINDS))
            raise ValueError(f"structured_output_kind must be one of: {choices}")
        object.__setattr__(self, "structured_output_kind", structured_kind)
        if not isinstance(self.usage, GenerationUsage):
            raise TypeError("usage must be GenerationUsage")
        object.__setattr__(
            self,
            "finish_reason",
            _optional_string(self.finish_reason, "finish_reason", 256),
        )
        object.__setattr__(
            self,
            "response_id",
            _optional_string(self.response_id, "response_id", 512),
        )
        _strict_int(self.chunks, "chunks", maximum=2**63 - 1)
        _strict_bool(self.terminal, "terminal")
        _strict_bool(self.done_received, "done_received")
        if not isinstance(self.timing, GenerationTiming):
            raise TypeError("timing must be GenerationTiming")
        if not isinstance(self.release, GenerationReleaseInfo):
            raise TypeError("release must be GenerationReleaseInfo")
        if self.error is not None and not isinstance(self.error, GenerationErrorInfo):
            raise TypeError("error must be GenerationErrorInfo or null")
        if self.state == GenerationState.COMPLETE and self.error is not None:
            raise ValueError("a complete result cannot contain an error")
        if self.state != GenerationState.COMPLETE and self.error is None:
            raise ValueError("a partial or cancelled result must contain an error")
        if self.state == GenerationState.COMPLETE and not self.terminal:
            raise ValueError("a complete result must have terminal stream evidence")
        if isinstance(self.warnings, str) or not isinstance(self.warnings, Sequence):
            raise TypeError("warnings must be a sequence of strings")
        warnings = tuple(
            _bounded_string(item, f"warnings[{index}]", MAX_WARNING_CHARS)
            for index, item in enumerate(self.warnings)
        )
        if len(warnings) > MAX_WARNING_COUNT:
            raise ValueError(f"warnings exceeds {MAX_WARNING_COUNT} entries")
        object.__setattr__(self, "warnings", warnings)
        _compact_json(
            self.as_dict(),
            maximum_bytes=MAX_RESULT_JSON_BYTES,
            name="generation result",
        )

    @property
    def success(self) -> bool:
        return self.state == GenerationState.COMPLETE

    @property
    def partial(self) -> bool:
        return self.state != GenerationState.COMPLETE

    @property
    def cancelled(self) -> bool:
        return self.state == GenerationState.CANCELLED

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "state": self.state.value,
            "response": self.response,
            "thinking": self.thinking,
            "requested_model": self.requested_model,
            "effective_model": self.effective_model,
            "profile_id": self.profile_id,
            "profile_sha256": self.profile_sha256,
            "seed": self.seed,
            "image_count": self.image_count,
            "structured_output_kind": self.structured_output_kind,
            "usage": self.usage.as_dict(),
            "finish_reason": self.finish_reason,
            "response_id": self.response_id,
            "chunks": self.chunks,
            "terminal": self.terminal,
            "done_received": self.done_received,
            "timing": self.timing.as_dict(),
            "release": self.release.as_dict(),
            "error": self.error.as_dict() if self.error else None,
            "warnings": list(self.warnings),
            "success": self.success,
            "partial": self.partial,
            "cancelled": self.cancelled,
        }

    def to_json(self) -> str:
        return _compact_json(
            self.as_dict(),
            maximum_bytes=MAX_RESULT_JSON_BYTES,
            name="generation result",
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> GenerationResult:
        expected = {
            "schema_version",
            "state",
            "response",
            "thinking",
            "requested_model",
            "effective_model",
            "profile_id",
            "profile_sha256",
            "seed",
            "image_count",
            "structured_output_kind",
            "usage",
            "finish_reason",
            "response_id",
            "chunks",
            "terminal",
            "done_received",
            "timing",
            "release",
            "error",
            "warnings",
            "success",
            "partial",
            "cancelled",
        }
        _exact_keys(value, expected, "generation result")
        _schema_version(value["schema_version"], "generation result", cls.SCHEMA_VERSION)
        usage = value["usage"]
        timing = value["timing"]
        release = value["release"]
        error = value["error"]
        if not isinstance(usage, Mapping):
            raise TypeError("generation result usage must be an object")
        if not isinstance(timing, Mapping):
            raise TypeError("generation result timing must be an object")
        if not isinstance(release, Mapping):
            raise TypeError("generation result release must be an object")
        if error is not None and not isinstance(error, Mapping):
            raise TypeError("generation result error must be an object or null")
        result = cls(
            state=value["state"],
            response=value["response"],
            thinking=value["thinking"],
            requested_model=value["requested_model"],
            effective_model=value["effective_model"],
            profile_id=value["profile_id"],
            profile_sha256=value["profile_sha256"],
            seed=value["seed"],
            image_count=value["image_count"],
            structured_output_kind=value["structured_output_kind"],
            usage=GenerationUsage.from_dict(usage),
            finish_reason=value["finish_reason"],
            response_id=value["response_id"],
            chunks=value["chunks"],
            terminal=value["terminal"],
            done_received=value["done_received"],
            timing=GenerationTiming.from_dict(timing),
            release=GenerationReleaseInfo.from_dict(release),
            error=GenerationErrorInfo.from_dict(error) if error else None,
            warnings=value["warnings"],
        )
        derived = {
            "success": result.success,
            "partial": result.partial,
            "cancelled": result.cancelled,
        }
        for field_name, expected_value in derived.items():
            if value[field_name] is not expected_value:
                raise ValueError(f"generation result {field_name} does not match state")
        return result

    @classmethod
    def from_json(cls, value: str | bytes) -> GenerationResult:
        return cls.from_dict(
            _json_object(value, "generation result", maximum_bytes=MAX_RESULT_JSON_BYTES)
        )


__all__ = [
    "ErrorCategory",
    "GenerationErrorInfo",
    "GenerationReleaseInfo",
    "GenerationRequestSpec",
    "GenerationResult",
    "GenerationState",
    "GenerationTiming",
    "GenerationUsage",
    "MAX_CONNECTION_SNAPSHOT_BYTES",
    "MAX_ERROR_MESSAGE_CHARS",
    "MAX_MODEL_TEXT_CHARS",
    "MAX_REQUEST_IMAGE_COUNT",
    "MAX_REQUEST_JSON_BYTES",
    "MAX_REQUEST_TEXT_CHARS",
    "MAX_RESULT_JSON_BYTES",
    "MAX_RESULT_TEXT_CHARS",
    "MAX_STOP_SEQUENCES",
    "MAX_STOP_SEQUENCE_CHARS",
    "MAX_TOKEN_BIAS_COUNT",
    "MAX_TOKEN_BIAS_JSON_BYTES",
    "MAX_WARNING_CHARS",
    "MAX_WARNING_COUNT",
    "PartialOutputPolicy",
    "ReleasePolicy",
    "SCHEMA_VERSION",
    "SamplingMode",
    "SamplingSettings",
    "ThinkingMode",
]
