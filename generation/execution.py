"""Strict canonical generation facade over the shared llama.cpp runtime."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from hashlib import sha256
from typing import Any
from urllib.parse import urlsplit

from .audio import (
    APPROVED_MODEL,
    APPROVED_PROJECTOR,
    ASR_MAX_TOKENS,
    SUPPORTED_ASR_PAIR,
    ApprovedPairIdentity,
    AudioPreparationCancelled,
    AudioResponseError,
    PreparedAudio,
    UnsupportedAudioModelError,
    parse_asr_response,
    prepare_audio,
    verify_approved_pair,
)
from .budget import (
    CONTEXT_LIMIT_SOURCE,
    INPUT_TOKEN_SOURCE,
    RequestBudget,
    effective_context_limit,
    enforce_request_budget,
)
from .captions import (
    MAX_CAPTION_IMAGE_BYTES,
    MAX_CAPTION_RESULT_BYTES,
    CaptionBatchSpec,
    caption_batch_outputs,
)
from .contracts import (
    MAX_TOKEN_BIAS_COUNT,
    MAX_TOKEN_BIAS_JSON_BYTES,
    ErrorCategory,
    GenerationErrorInfo,
    GenerationReleaseInfo,
    GenerationRequestSpec,
    GenerationResult,
    GenerationState,
    GenerationTiming,
    GenerationUsage,
    PartialOutputPolicy,
    ReleasePolicy,
    SamplingMode,
    SamplingSettings,
    ThinkingMode,
)
from .messages import ConversationMessages
from .payloads import normalize_structured_output
from .profiles import FREEFORM_PROFILE, TaskProfileSnapshot, apply_task_profile
from .types import parse_text_list

if "." in __package__:
    from ..models.identity import RouterIdentityError
    from ..runtime.client import (
        ConnectionConfig,
        DeadlineExceeded,
        LlamaClientError,
        LlamaServerClient,
        OperationCancelled,
        ResponseBodyLimitError,
        ResponseProtocolError,
        StreamControlSupport,
        redact_secrets,
    )
    from ..runtime.live_generation import (
        CancelScope,
        ExecutionIdentity,
        LiveGenerationRegistry,
        get_live_generation_registry,
    )
    from ..runtime.manager import get_server_manager
    from ..runtime.service import ReleaseResult
    from ..runtime.streaming import StreamResult
else:  # standalone pure-module tests
    from models.identity import RouterIdentityError
    from runtime.client import (
        ConnectionConfig,
        DeadlineExceeded,
        LlamaClientError,
        LlamaServerClient,
        OperationCancelled,
        ResponseBodyLimitError,
        ResponseProtocolError,
        StreamControlSupport,
        redact_secrets,
    )
    from runtime.live_generation import (
        CancelScope,
        ExecutionIdentity,
        LiveGenerationRegistry,
        get_live_generation_registry,
    )
    from runtime.manager import get_server_manager
    from runtime.service import ReleaseResult
    from runtime.streaming import StreamResult

RUNNING_MODEL = "(use running model)"
_DEFAULT_LIVE_REGISTRY = object()
_CUSTOM_SAMPLER_FIELDS = (
    "temperature",
    "top_p",
    "top_k",
    "min_p",
    "repeat_penalty",
    "presence_penalty",
    "frequency_penalty",
)
_REMOTE_AUTH_BINDINGS_ENV = "LLAMACPP_REMOTE_AUTH_BINDINGS"
_MAX_REMOTE_AUTH_BINDINGS_BYTES = 65_536
_MAX_REMOTE_AUTH_BINDINGS = 128
_IMAGE_CAPABILITY_PROBE_SECONDS = 2.0
_VISION_UNAVAILABLE_MESSAGE = (
    "the selected llama.cpp model reports that vision is unavailable; "
    "use a vision-capable model with its matching projector before connecting an image"
)
_EXPLICIT_TEXT_ONLY_IMAGE_MESSAGE = (
    "image input is connected, but Vision Projector is '(none - text only)'; "
    "select '(auto)' or an exact matching projector, or disconnect the image"
)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


class CanonicalGenerationError(RuntimeError):
    """One stable, redacted error for the strict Generate surface."""

    def __init__(self, info: GenerationErrorInfo) -> None:
        if not isinstance(info, GenerationErrorInfo):
            raise TypeError("info must be GenerationErrorInfo")
        self.info = info
        super().__init__(f"llama.cpp Generate [{info.category.value}]: {info.message}")

    @property
    def category(self) -> ErrorCategory:
        return self.info.category


def _looks_like_tls_failure(message: str) -> bool:
    return (
        "certificate" in message or "ssl" in message or re.search(r"\btls\b", message) is not None
    )


def _safe_message(
    value: object,
    *,
    connection: ConnectionConfig | None = None,
    fallback: str,
) -> str:
    text = str(value or "").strip()
    if connection is not None:
        text = redact_secrets(text, connection.secrets)
    # Exception strings can contain complete upstream bodies or process logs.
    # The strict facade retains only one bounded actionable line.
    line = next((item.strip() for item in text.splitlines() if item.strip()), "")
    return (line or fallback)[:4096]


def _error(
    category: ErrorCategory,
    value: object,
    *,
    connection: ConnectionConfig | None = None,
    fallback: str,
    status_code: int | None = None,
    retryable: bool = False,
) -> CanonicalGenerationError:
    return CanonicalGenerationError(
        GenerationErrorInfo(
            category=category,
            message=_safe_message(value, connection=connection, fallback=fallback),
            status_code=status_code,
            retryable=retryable,
        )
    )


def _classify_exception(
    exc: Exception,
    *,
    connection: ConnectionConfig | None,
) -> CanonicalGenerationError:
    if isinstance(exc, CanonicalGenerationError):
        return exc
    status_code = getattr(exc, "status_code", None)
    message = str(exc).casefold()
    if status_code in {401, 403}:
        category = ErrorCategory.AUTHENTICATION
    elif isinstance(exc, (AudioPreparationCancelled, OperationCancelled)):
        category = ErrorCategory.CANCELLED
    elif isinstance(exc, UnsupportedAudioModelError):
        category = ErrorCategory.CAPABILITY_UNSUPPORTED
    elif isinstance(exc, (AudioResponseError, ResponseProtocolError, ResponseBodyLimitError)):
        category = ErrorCategory.PROTOCOL
    elif _looks_like_tls_failure(message):
        category = ErrorCategory.TLS
    elif status_code == 404 and "model" in message:
        category = ErrorCategory.MODEL_MISSING
    elif isinstance(exc, (DeadlineExceeded, TimeoutError)):
        category = ErrorCategory.TIMEOUT
    elif isinstance(exc, LlamaClientError):
        category = ErrorCategory.SERVER if status_code is not None else ErrorCategory.TRANSPORT
    elif isinstance(exc, (TypeError, ValueError)):
        category = ErrorCategory.INVALID_REQUEST
    elif isinstance(exc, RuntimeError):
        category = ErrorCategory.RUNTIME_UNAVAILABLE
    else:
        category = ErrorCategory.SERVER
    if isinstance(exc, LlamaClientError):
        public_message = {
            ErrorCategory.AUTHENTICATION: (
                "llama-server rejected local authentication; check the configured API key "
                "environment variable"
            ),
            ErrorCategory.TLS: (
                "TLS verification failed while contacting llama-server; check the local "
                "certificate or Verify TLS setting"
            ),
            ErrorCategory.MODEL_MISSING: (
                "the requested model was not found by the selected llama.cpp runtime"
            ),
            ErrorCategory.TIMEOUT: "the llama.cpp request exceeded its configured deadline",
            ErrorCategory.TRANSPORT: "could not reach the local llama-server endpoint",
            ErrorCategory.SERVER: "llama-server rejected or failed the request",
        }.get(category, "the llama.cpp request failed")
    else:
        public_message = exc
    return _error(
        category,
        public_message,
        connection=connection,
        fallback=type(exc).__name__,
        status_code=status_code if isinstance(status_code, int) else None,
        retryable=category in {ErrorCategory.TIMEOUT, ErrorCategory.TRANSPORT},
    )


def _stream_error(
    result: StreamResult,
    connection: ConnectionConfig,
    *,
    image_unsupported_message: str = _VISION_UNAVAILABLE_MESSAGE,
) -> CanonicalGenerationError:
    message = (result.error_message or "").casefold()
    if result.status_code in {401, 403}:
        category = ErrorCategory.AUTHENTICATION
    elif result.error_type == "image_unsupported":
        category = ErrorCategory.CAPABILITY_UNSUPPORTED
    elif result.error_type == "model_missing" or (result.status_code == 404 and "model" in message):
        category = ErrorCategory.MODEL_MISSING
    elif result.error_type == "timeout":
        category = ErrorCategory.TIMEOUT
    elif result.error_type == "transport":
        category = (
            ErrorCategory.TLS if _looks_like_tls_failure(message) else ErrorCategory.TRANSPORT
        )
    elif result.error_type in {"protocol", "incomplete", "resource"}:
        category = ErrorCategory.PROTOCOL
    elif result.error_type == "cancelled" or result.cancelled:
        category = ErrorCategory.CANCELLED
    else:
        category = ErrorCategory.SERVER
    public_message = {
        ErrorCategory.AUTHENTICATION: (
            "llama-server rejected local authentication; check the configured API key "
            "environment variable"
        ),
        ErrorCategory.CAPABILITY_UNSUPPORTED: image_unsupported_message,
        ErrorCategory.MODEL_MISSING: (
            "the requested model was not found by the selected llama.cpp runtime"
        ),
        ErrorCategory.TIMEOUT: "the llama.cpp generation exceeded its configured deadline",
        ErrorCategory.TLS: (
            "TLS verification failed while contacting llama-server; check the local "
            "certificate or Verify TLS setting"
        ),
        ErrorCategory.TRANSPORT: "the local llama-server connection failed during generation",
        ErrorCategory.PROTOCOL: (
            "llama-server stream exceeded the local resource limit"
            if result.error_type == "resource"
            else (
                "llama-server returned an invalid streamed response"
                if result.error_type == "protocol"
                else "llama-server stream ended without terminal evidence"
            )
        ),
        ErrorCategory.CANCELLED: "generation was cancelled before terminal completion",
        ErrorCategory.SERVER: "llama-server rejected or failed the generation request",
    }[category]
    return _error(
        category,
        public_message,
        connection=connection,
        fallback="generation failed",
        status_code=result.status_code,
        retryable=category
        in {ErrorCategory.TIMEOUT, ErrorCategory.TRANSPORT, ErrorCategory.SERVER},
    )


def _stream_cleanup_warning(result: StreamResult) -> str | None:
    cleanup = result.stream_cleanup
    if cleanup is None:
        return None
    try:
        value = cleanup.as_dict() if hasattr(cleanup, "as_dict") else cleanup
        if not isinstance(value, Mapping):
            return "upstream stream cleanup status was unavailable"
        attempts = value.get("attempts")
        confirmed = value.get("confirmed")
        if type(attempts) is not int or attempts <= 0:
            return "upstream stream cleanup was not attempted"
        if confirmed is True:
            return None
        warning = f"upstream stream cleanup was not confirmed after {attempts} attempt(s)"
        last_error_type = value.get("last_error_type")
        if isinstance(last_error_type, str) and last_error_type:
            warning += f" ({last_error_type[:128]})"
        return warning
    except Exception:
        return "upstream stream cleanup status was unavailable"


def _is_loopback_host(hostname: str | None) -> bool:
    if not hostname:
        return False
    normalized = hostname.casefold().rstrip(".")
    if normalized == "localhost" or normalized.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _credential_origin(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("credential origin must be an absolute HTTP or HTTPS URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("credential origin must not contain credentials, query, or fragment")
    if parsed.path not in {"", "/"}:
        raise ValueError("credential origin must not contain a path")
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    hostname = parsed.hostname.casefold().rstrip(".")
    if ":" in hostname:
        hostname = f"[{hostname}]"
    return f"{parsed.scheme.casefold()}://{hostname}:{port}"


def _remote_auth_bindings() -> Mapping[str, str]:
    encoded = os.environ.get(_REMOTE_AUTH_BINDINGS_ENV, "").encode("utf-8")
    if not encoded:
        return {}
    if len(encoded) > _MAX_REMOTE_AUTH_BINDINGS_BYTES:
        raise ValueError(f"{_REMOTE_AUTH_BINDINGS_ENV} exceeds its local size limit")
    parsed = json.loads(encoded)
    if not isinstance(parsed, Mapping) or len(parsed) > _MAX_REMOTE_AUTH_BINDINGS:
        raise ValueError(f"{_REMOTE_AUTH_BINDINGS_ENV} must be a bounded JSON object")
    bindings: dict[str, str] = {}
    for origin, env_name in parsed.items():
        if not isinstance(origin, str) or not isinstance(env_name, str):
            raise ValueError(f"{_REMOTE_AUTH_BINDINGS_ENV} keys and values must be strings")
        if not re.fullmatch(r"LLAMACPP_API_KEY(?:_[A-Z0-9_]+)?", env_name):
            raise ValueError(f"{_REMOTE_AUTH_BINDINGS_ENV} contains an invalid key name")
        normalized = _credential_origin(origin)
        if normalized in bindings:
            raise ValueError(f"{_REMOTE_AUTH_BINDINGS_ENV} contains a duplicate origin")
        bindings[normalized] = env_name
    return bindings


def _validate_canonical_transport(
    connection: ConnectionConfig,
    *,
    api_key_env: str,
) -> None:
    parsed = urlsplit(connection.base_url)
    if not connection.secrets or _is_loopback_host(parsed.hostname):
        return
    if parsed.scheme == "http":
        raise _error(
            ErrorCategory.INVALID_REQUEST,
            "Authenticated canonical generation requires HTTPS outside loopback",
            connection=connection,
            fallback="insecure authenticated endpoint",
        )
    if connection.tls.verify is False:
        raise _error(
            ErrorCategory.INVALID_REQUEST,
            "Authenticated non-loopback generation requires TLS certificate verification",
            connection=connection,
            fallback="remote authenticated TLS verification is disabled",
        )
    try:
        hostname = parsed.hostname.casefold().rstrip(".")
        if ":" in hostname:
            hostname = f"[{hostname}]"
        port = parsed.port
        if port is None:
            port = 443
        origin = f"{parsed.scheme}://{hostname}:{port}"
        approved_env = _remote_auth_bindings().get(origin)
    except (TypeError, ValueError, json.JSONDecodeError):
        raise _error(
            ErrorCategory.INVALID_REQUEST,
            f"invalid server-side {_REMOTE_AUTH_BINDINGS_ENV} configuration",
            connection=connection,
            fallback="invalid remote credential binding",
        ) from None
    if not api_key_env or approved_env != api_key_env:
        raise _error(
            ErrorCategory.INVALID_REQUEST,
            (
                "Authenticated non-loopback generation requires an exact server-side "
                f"{_REMOTE_AUTH_BINDINGS_ENV} origin-to-key binding"
            ),
            connection=connection,
            fallback="remote credential destination is not approved",
        )


def _connection_values(
    connection: Any,
    *,
    server_url: str,
    model: str,
    api_key_env: str,
    verify_tls: bool,
    request_timeout: int,
) -> tuple[dict[str, Any], str, str, str, bool, int]:
    if connection is not None and server_url.strip():
        raise _error(
            ErrorCategory.INVALID_REQUEST,
            "Connect either llama.cpp Connection or Server URL, not both",
            fallback="ambiguous connection inputs",
        )
    if connection is not None:
        try:
            snapshot = connection.as_dict()
        except AttributeError as exc:
            raise _error(
                ErrorCategory.INVALID_REQUEST,
                exc,
                fallback="connection input is not a llama.cpp Connection",
            ) from exc
        endpoint = str(getattr(connection, "server_url", ""))
        connection_model = str(getattr(connection, "model", ""))
        selected_model = (
            connection_model
            if not model.strip() or model.strip() == RUNNING_MODEL
            else model.strip()
        )
        return (
            dict(snapshot),
            endpoint,
            selected_model,
            str(getattr(connection, "api_key_env", "")),
            bool(getattr(connection, "verify_tls", True)),
            int(getattr(connection, "request_timeout", request_timeout)),
        )

    selected_model = model.strip()
    if selected_model == RUNNING_MODEL:
        selected_model = ""
    snapshot = {
        "schema_version": 1,
        "server_url": server_url.strip(),
        "model": selected_model,
        "api_key_env": api_key_env.strip(),
        "verify_tls": verify_tls,
        "request_timeout": request_timeout,
    }
    return (
        snapshot,
        server_url.strip(),
        selected_model,
        api_key_env.strip(),
        verify_tls,
        request_timeout,
    )


def _normalize_images(images: Sequence[str]) -> tuple[str, ...]:
    if isinstance(images, (str, bytes)) or not isinstance(images, Sequence):
        raise _error(
            ErrorCategory.INVALID_REQUEST,
            "images must be a sequence of encoded image URLs",
            fallback="invalid image input",
        )
    normalized = []
    for index, image in enumerate(images):
        if not isinstance(image, str) or not image:
            raise _error(
                ErrorCategory.INVALID_REQUEST,
                f"image {index + 1} is not an encoded image URL",
                fallback="invalid image input",
            )
        normalized.append(image)
    return tuple(normalized)


def _normalize_token_ban(value: Any) -> list[list[str | bool]] | None:
    if value is None:
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError("token ban must be a sequence of [text, special] entries")
    if len(value) > MAX_TOKEN_BIAS_COUNT:
        raise ValueError(f"token ban exceeds {MAX_TOKEN_BIAS_COUNT} entries")
    normalized: list[list[str | bool]] = []
    serialized_bytes = 2  # opening and closing JSON list delimiters
    for index, entry in enumerate(value):
        if isinstance(entry, (str, bytes)) or not isinstance(entry, Sequence) or len(entry) != 2:
            raise ValueError(f"token ban entry {index + 1} must contain text and a Boolean")
        token, special = entry
        if not isinstance(token, str) or not token or len(token) > 4096:
            raise ValueError(f"token ban entry {index + 1} text is invalid")
        try:
            token.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError(f"token ban entry {index + 1} text is not valid UTF-8") from exc
        if type(special) is not bool:
            raise ValueError(f"token ban entry {index + 1} flag must be a Boolean")
        encoded_entry = json.dumps(
            [token, special],
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        serialized_bytes += len(encoded_entry) + (1 if normalized else 0)
        if serialized_bytes > MAX_TOKEN_BIAS_JSON_BYTES:
            raise ValueError(f"token ban exceeds {MAX_TOKEN_BIAS_JSON_BYTES} serialized bytes")
        normalized.append([token, special])
    return normalized or None


def build_canonical_payload(
    request: GenerationRequestSpec,
    *,
    images: Sequence[str] = (),
    structured_output: Any = None,
    token_ban: Any = None,
    audio: PreparedAudio | None = None,
    include_usage: bool = False,
) -> dict[str, Any]:
    """Build the strict request without changing legacy GenerationOptions."""

    encoded_images = _normalize_images(images)
    normalized_token_ban = _normalize_token_ban(token_ban)
    if len(encoded_images) != request.image_count:
        raise _error(
            ErrorCategory.INVALID_REQUEST,
            "encoded image count does not match the request specification",
            fallback="image count mismatch",
        )

    user_content: str | list[dict[str, Any]]
    if encoded_images:
        user_content = [
            {"type": "image_url", "image_url": {"url": image}} for image in encoded_images
        ]
        user_content.append({"type": "text", "text": request.prompt})
    else:
        user_content = request.prompt

    messages: list[dict[str, Any]] = []
    if request.system_prompt != "":
        messages.append({"role": "system", "content": request.system_prompt})
    if request.messages is not None:
        messages.extend(message.as_dict() for message in request.messages.messages)
    messages.append({"role": "user", "content": user_content})
    if request.operation is not None:
        if (
            not isinstance(audio, PreparedAudio)
            or request.operation["wav_sha256"] != audio.wav_sha256
        ):
            raise ValueError("audio payload does not match operation metadata")
        messages = [{"role": "user", "content": [audio.content_part()]}]
    elif audio is not None:
        raise ValueError("audio payload requires explicit operation metadata")

    payload: dict[str, Any] = {
        "stream": True,
        "messages": messages,
        "max_tokens": request.max_tokens,
        "seed": request.seed,
        "cache_prompt": request.cache_prompt,
    }
    if include_usage:
        payload["stream_options"] = {"include_usage": True}
    if request.requested_model:
        payload["model"] = request.requested_model
    if request.thinking_mode != ThinkingMode.AUTO:
        payload["chat_template_kwargs"] = {
            "enable_thinking": request.thinking_mode == ThinkingMode.ON
        }
    if request.sampling.mode == SamplingMode.CUSTOM:
        for field_name in _CUSTOM_SAMPLER_FIELDS:
            payload[field_name] = getattr(request.sampling, field_name)
    if request.stop_sequences:
        payload["stop"] = list(request.stop_sequences)
    if normalized_token_ban:
        payload["logit_bias"] = normalized_token_ban

    constraint = normalize_structured_output(structured_output)
    if constraint is not None:
        if constraint.kind != request.structured_output_kind:
            raise _error(
                ErrorCategory.INVALID_REQUEST,
                "structured-output kind changed during request preparation",
                fallback="structured-output mismatch",
            )
        constraint.apply(payload)
        # Python's encoder otherwise accepts NaN and Infinity, which are not
        # valid JSON and make a supposedly strict request nonportable.
        json.dumps(payload.get("response_format", payload.get("grammar")), allow_nan=False)
    return payload


def _release_info(
    result: ReleaseResult,
    *,
    elapsed_seconds: float,
    connection: ConnectionConfig,
) -> GenerationReleaseInfo:
    def wire(value: object) -> str | None:
        if value is None:
            return None
        return str(getattr(value, "value", value))

    terminal = bool(result.terminal)
    success = bool(result.success and terminal)
    error = (
        None
        if success
        else GenerationErrorInfo(
            ErrorCategory.RELEASE_FAILED,
            "terminal runtime release failed; inspect local runtime diagnostics",
        )
    )
    return GenerationReleaseInfo(
        policy=ReleasePolicy.RELEASE_AFTER_GENERATION,
        status=wire(result.status) or "failed",
        mode=wire(result.mode),
        target_model=result.target_model,
        request_id=result.request_id,
        operation_id=result.operation_id,
        scope=wire(result.scope),
        coalesced=bool(result.coalesced),
        superseded_by=result.superseded_by,
        driver_memory_verified=bool(result.driver_memory_verified),
        terminal=terminal,
        success=success,
        elapsed_seconds=elapsed_seconds,
        released_models=tuple(result.released_models),
        error=error,
    )


def _failed_release_info(
    error: object,
    *,
    status: str,
    connection: ConnectionConfig,
    router: bool,
    target_model: str | None,
    policy: ReleasePolicy = ReleasePolicy.RELEASE_AFTER_GENERATION,
    elapsed_seconds: float | None = None,
) -> GenerationReleaseInfo:
    return GenerationReleaseInfo(
        policy=policy,
        status=status,
        mode="router" if router else "direct",
        target_model=target_model if router else None,
        scope="router_model" if router else "direct_runtime",
        terminal=False,
        success=False,
        elapsed_seconds=elapsed_seconds,
        error=GenerationErrorInfo(
            ErrorCategory.RELEASE_FAILED,
            "terminal runtime release failed; inspect local runtime diagnostics",
        ),
    )


def _accepted_release_info(
    handle: object,
    *,
    router: bool,
    target_model: str | None,
) -> GenerationReleaseInfo:
    target = getattr(handle, "target", None)
    scope = getattr(getattr(target, "scope", None), "value", None)
    selected_model = getattr(target, "model_id", None)
    return GenerationReleaseInfo(
        policy=ReleasePolicy.RELEASE_AFTER_GENERATION,
        status="accepted_continuing",
        mode="router" if router else "direct",
        target_model=(selected_model or target_model) if router else None,
        request_id=getattr(handle, "request_id", None),
        operation_id=getattr(handle, "operation_id", None),
        scope=scope or ("router_model" if router else "direct_runtime"),
        coalesced=bool(getattr(handle, "coalesced", False)),
        terminal=False,
        success=False,
    )


def _with_release_failure(
    primary: Exception,
    release_error: Exception,
    *,
    connection: ConnectionConfig,
) -> CanonicalGenerationError:
    normalized = _classify_exception(primary, connection=connection)
    release_message = "terminal runtime release failed; inspect local runtime diagnostics"
    info = normalized.info
    return CanonicalGenerationError(
        GenerationErrorInfo(
            category=info.category,
            message=(f"{info.message}; terminal runtime release also failed: {release_message}")[
                :4096
            ],
            status_code=info.status_code,
            retryable=info.retryable,
        )
    )


class CanonicalGenerationExecutor:
    """Dependency-injected canonical generation coordinator."""

    def __init__(
        self,
        *,
        manager: Any = None,
        client_factory: Callable[[ConnectionConfig], Any] = LlamaServerClient,
        live_registry: LiveGenerationRegistry | None | object = _DEFAULT_LIVE_REGISTRY,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.manager = manager or get_server_manager()
        self.client_factory = client_factory
        self.live_registry = (
            get_live_generation_registry()
            if live_registry is _DEFAULT_LIVE_REGISTRY
            else live_registry
        )
        self.clock = clock

    def budget(self, prompt: str, **options: Any) -> dict[str, Any]:
        """Explicit observation; a later node must check its own request again."""
        policy = options.pop("budget_policy", "report")
        return self.generate(
            prompt,
            budget_policy="report" if policy == "off" else policy,
            _budget_only=True,
            **options,
        )

    def captions(
        self,
        prompt: str,
        *,
        item_count: int,
        prepare_images: Callable,
        ids_json: str = "",
        prompts_json: str = "",
        seeds_json: str = "",
        seed: int = 0,
        progress: Callable[[int, int], Any] | None = None,
        **options: Any,
    ) -> tuple[list[str], str]:
        spec = CaptionBatchSpec.from_inputs(
            item_count,
            prompt,
            seed,
            ids_json=ids_json,
            prompts_json=prompts_json,
            seeds_json=seeds_json,
        )
        return self.generate(
            spec.rows[0].prompt,
            seed=spec.rows[0].seed,
            _caption_spec=spec,
            _caption_prepare=prepare_images,
            _caption_progress=progress,
            **options,
        )

    def transcribe(
        self,
        audio: Any,
        *,
        supported_pair: str = SUPPORTED_ASR_PAIR,
        server_url: str = "",
        request_timeout: int = 60,
        release_after_generation: bool = False,
        identity: ExecutionIdentity | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> tuple[str, str, str, GenerationResult, str]:
        raw, _, result = self.generate(
            "",
            server_url=server_url,
            request_timeout=request_timeout,
            release_after_generation=release_after_generation,
            identity=identity,
            cancel_check=cancel_check,
            max_tokens=ASR_MAX_TOKENS,
            thinking_mode=ThinkingMode.OFF,
            sampling_mode=SamplingMode.CUSTOM,
            temperature=0.0,
            seed=0,
            _audio_input=audio,
            _supported_pair=supported_pair,
        )
        parsed = parse_asr_response(raw)
        metadata = dict(result.operation or {})
        metadata.update(
            language=parsed.language,
            prefix_status=parsed.prefix_status,
            warnings=list(parsed.warnings),
        )
        return (
            parsed.transcript,
            raw,
            parsed.language,
            result,
            json.dumps(metadata, ensure_ascii=False),
        )

    def generate(
        self,
        prompt: str,
        *,
        connection: Any = None,
        profile: TaskProfileSnapshot | None = None,
        messages: ConversationMessages | None = None,
        server_url: str = "",
        model: str = RUNNING_MODEL,
        system_prompt: str = "",
        thinking_mode: ThinkingMode | str = ThinkingMode.AUTO,
        max_tokens: int = 2048,
        sampling_mode: SamplingMode | str = SamplingMode.DEFAULT,
        temperature: float = 0.7,
        top_p: float = 0.9,
        top_k: int = 40,
        min_p: float = 0.05,
        repeat_penalty: float = 1.1,
        presence_penalty: float = 0.0,
        frequency_penalty: float = 0.0,
        seed: int = 0,
        cache_prompt: bool = False,
        stop_sequences: str | Sequence[str] = "",
        api_key_env: str = "LLAMACPP_API_KEY",
        verify_tls: bool = True,
        request_timeout: int = 300,
        images: Sequence[str] = (),
        prepare_images: Callable[[], Sequence[str]] | None = None,
        release_after_generation: bool = False,
        partial_output_policy: PartialOutputPolicy | str = (PartialOutputPolicy.RAISE_ERROR),
        structured_output: Any = None,
        token_ban: Any = None,
        identity: ExecutionIdentity | None = None,
        cancel_check: Callable[[], bool] | None = None,
        _audio_input: Any = None,
        _supported_pair: str | None = None,
        budget_policy: str = "off",
        _budget_only: bool = False,
        _caption_spec: CaptionBatchSpec | None = None,
        _caption_prepare: Callable | None = None,
        _caption_progress: Callable[[int, int], Any] | None = None,
    ) -> tuple[str, str, GenerationResult]:
        started = self.clock()
        resolved_connection: ConnectionConfig | None = None
        client: Any = None
        live: Any = None
        stream_result: StreamResult | None = None
        stream_cleanup_snapshot: object | None = None
        release_info = GenerationReleaseInfo()
        warnings: list[str] = []
        cleanup_warning: str | None = None
        deadline_at: float | None = None
        prepared_audio: PreparedAudio | None = None
        request_budget: RequestBudget | None = None
        budget_payload_sha256: str | None = None
        caption_rows: list[dict[str, Any]] = []
        caption_state = "complete"
        caption_failure: CanonicalGenerationError | None = None

        def remaining(*, require_positive: bool, stage: str) -> float:
            if deadline_at is None:  # pragma: no cover - internal ordering invariant
                raise RuntimeError("generation deadline is not initialized")
            budget = max(0.0, deadline_at - self.clock())
            if require_positive and budget <= 0:
                raise _error(
                    ErrorCategory.TIMEOUT,
                    f"overall request deadline expired before {stage}",
                    connection=resolved_connection,
                    fallback="overall request deadline expired",
                    retryable=True,
                )
            return budget

        def cancelled() -> bool:
            if live is not None and live.token.cancelled:
                return True
            return bool(cancel_check()) if cancel_check is not None else False

        def caption_progress(completed: int) -> None:
            if _caption_progress is not None and _caption_spec is not None:
                try:
                    _caption_progress(completed, _caption_spec.item_count)
                except Exception:
                    pass  # Optional host progress must not bypass operation cleanup.

        try:
            if identity is not None and self.live_registry is not None:
                try:
                    live = self.live_registry.begin(
                        identity,
                        cancel_scope=(
                            CancelScope.PROMPT
                            if identity.prompt_id is not None
                            else CancelScope.NONE
                        ),
                    )
                except Exception:
                    # Live state is additive, including during request and
                    # image preparation. It must never block headless use.
                    live = None
            if not isinstance(model, str):
                raise TypeError("model must be a string")
            if budget_policy not in {"off", "report", "enforce"}:
                raise ValueError("budget_policy must be off, report or enforce")
            profile = profile or FREEFORM_PROFILE
            if not isinstance(profile, TaskProfileSnapshot):
                raise _error(
                    ErrorCategory.INVALID_REQUEST,
                    "profile input is not a saved llama.cpp task profile",
                    fallback="invalid task profile",
                )
            if messages is not None:
                if not isinstance(messages, ConversationMessages):
                    raise TypeError("messages input is not a llama.cpp text history")
                if messages.has_system and (system_prompt != "" or profile.system_prompt != ""):
                    raise ValueError(
                        "history system message conflicts with system prompt or profile"
                    )
            effective_prompt, effective_system = apply_task_profile(
                profile,
                prompt,
                system_prompt,
            )
            (
                connection_snapshot,
                endpoint,
                selected_model,
                resolved_api_key_env,
                resolved_verify_tls,
                resolved_timeout,
            ) = _connection_values(
                connection,
                server_url=server_url,
                model=model,
                api_key_env=api_key_env,
                verify_tls=verify_tls,
                request_timeout=request_timeout,
            )
            deadline_at = started + float(resolved_timeout)

            def check_operation() -> None:
                if cancelled():
                    raise OperationCancelled("operation cancelled")
                remaining(require_positive=True, stage="request preparation")

            if _caption_spec is not None:
                if _budget_only or _supported_pair is not None or budget_policy != "off":
                    raise ValueError("caption groups do not support another operation mode")
                if images or prepare_images is not None or not callable(_caption_prepare):
                    raise ValueError(
                        "caption groups require exactly one image preparation callback"
                    )
                caption_progress(0)
                images = _caption_prepare(check_operation)
                if len(images) != _caption_spec.item_count:
                    raise ValueError("caption images must exactly match the item count")
                if sum(len(item.encode("utf-8")) for item in images) > MAX_CAPTION_IMAGE_BYTES:
                    raise ValueError("caption images exceed the encoded group limit")
                caption_rows = [
                    {
                        "item_id": item.item_id,
                        "seed": item.seed,
                        "state": "not_attempted",
                        "response": "",
                        "thinking": "",
                        "result": None,
                        "error": None,
                    }
                    for item in _caption_spec.rows
                ]
            elif prepare_images is not None:
                if images:
                    raise ValueError("supply encoded images or image preparation, not both")
                images = prepare_images()
            if _supported_pair is not None:
                if _supported_pair != SUPPORTED_ASR_PAIR:
                    raise UnsupportedAudioModelError("select the explicitly supported ASR pair")
                prepared_audio = prepare_audio(
                    _audio_input,
                    cancel_check=cancelled,
                    deadline_check=lambda: remaining(
                        require_positive=True, stage="audio preparation"
                    ),
                )
            exact_model = selected_model or None
            normalized_images = _normalize_images(images)
            caption_images = normalized_images if _caption_spec is not None else ()
            if _caption_spec is not None:
                normalized_images = normalized_images[:1]
            try:
                constraint = normalize_structured_output(structured_output)
                selected_sampling_mode = SamplingMode(sampling_mode)
                sampling = (
                    SamplingSettings(
                        mode=selected_sampling_mode,
                        temperature=temperature,
                        top_p=top_p,
                        top_k=top_k,
                        min_p=min_p,
                        repeat_penalty=repeat_penalty,
                        presence_penalty=presence_penalty,
                        frequency_penalty=frequency_penalty,
                    )
                    if selected_sampling_mode == SamplingMode.CUSTOM
                    else SamplingSettings(mode=SamplingMode.DEFAULT)
                )
                normalized_token_ban = _normalize_token_ban(token_ban)
                request = GenerationRequestSpec(
                    connection=connection_snapshot,
                    prompt=effective_prompt,
                    system_prompt=effective_system,
                    requested_model=exact_model,
                    profile_id=profile.profile_id,
                    profile_sha256=profile.content_sha256,
                    thinking_mode=thinking_mode,
                    sampling=sampling,
                    max_tokens=max_tokens,
                    seed=seed,
                    cache_prompt=cache_prompt,
                    stop_sequences=tuple(parse_text_list(stop_sequences)),
                    image_count=len(normalized_images),
                    structured_output_kind=constraint.kind if constraint else None,
                    token_bias_count=(len(normalized_token_ban) if normalized_token_ban else 0),
                    release_policy=(
                        ReleasePolicy.RELEASE_AFTER_GENERATION
                        if release_after_generation
                        else ReleasePolicy.REUSE
                    ),
                    partial_output_policy=partial_output_policy,
                    messages=messages,
                    operation=(
                        prepared_audio.operation_metadata(
                            ApprovedPairIdentity(APPROVED_MODEL.sha256, APPROVED_PROJECTOR.sha256)
                        )
                        if prepared_audio is not None
                        else None
                    ),
                )
                # Validate all pure payload inputs before admitting a managed
                # lease. The exact router ID is substituted after admission.
                build_canonical_payload(
                    request,
                    images=normalized_images,
                    structured_output=constraint,
                    token_ban=normalized_token_ban,
                    audio=prepared_audio,
                )
            except CanonicalGenerationError:
                raise
            except Exception as exc:
                raise _error(
                    ErrorCategory.INVALID_REQUEST,
                    exc,
                    connection=resolved_connection,
                    fallback="invalid generation request",
                ) from exc

            caption_requests = []
            if _caption_spec is not None:
                for item, image in zip(_caption_spec.rows, caption_images, strict=True):
                    item_prompt, item_system = apply_task_profile(
                        profile, item.prompt, system_prompt
                    )
                    item_request = replace(
                        request,
                        prompt=item_prompt,
                        system_prompt=item_system,
                        seed=item.seed,
                        image_count=1,
                    )
                    build_canonical_payload(
                        item_request,
                        images=(image,),
                        structured_output=constraint,
                        token_ban=normalized_token_ban,
                    )
                    caption_requests.append(item_request)

            try:
                resolved_connection, managed = self.manager.connection_for(
                    endpoint,
                    api_key_env=resolved_api_key_env,
                    verify_tls=resolved_verify_tls,
                    request_timeout=resolved_timeout,
                )
            except Exception as exc:
                raise _error(
                    ErrorCategory.RUNTIME_UNAVAILABLE,
                    exc,
                    fallback="could not resolve llama-server connection",
                ) from exc
            _validate_canonical_transport(
                resolved_connection,
                api_key_env=resolved_api_key_env,
            )
            if release_after_generation and not managed:
                raise _error(
                    ErrorCategory.INVALID_REQUEST,
                    "Release After Generation requires a positively owned runtime",
                    connection=resolved_connection,
                    fallback="attached endpoints cannot be released",
                )

            runtime_service = self.manager.runtime_service
            lease = None
            router_mode = False
            release_handle = None
            primary: BaseException | None = None
            stream_failure: CanonicalGenerationError | None = None
            release_wait_error: Exception | None = None
            generation_started = self.clock()
            first_update: float | None = None
            stream_control: Any = None
            image_unsupported_message = _VISION_UNAVAILABLE_MESSAGE

            def on_update(update: Any) -> None:
                nonlocal first_update
                if first_update is None and (update.content or update.thinking):
                    first_update = self.clock()
                if live is not None:
                    live.on_stream_update(update)

            def resolve_router_model(requested_model: str) -> str:
                return self.manager.resolve_model_id(
                    requested_model,
                    timeout=remaining(
                        require_positive=True,
                        stage="router model resolution",
                    ),
                )

            def check_caption_release() -> None:
                if (
                    _caption_spec is not None
                    and lease is not None
                    and getattr(runtime_service, "release_pending", False)
                ):
                    # A global release supersedes this group's dormant scoped
                    # intent. Stop at a request boundary and let the one finalizer
                    # exit the lease so the accepted release can complete.
                    raise OperationCancelled("caption group stopped for requested runtime release")

            def run_stream(payload: Mapping[str, Any]) -> None:
                nonlocal stream_control, stream_result
                check_caption_release()
                probe_budget = min(
                    2.0,
                    remaining(require_positive=True, stage="stream capability probe"),
                )
                try:
                    probe, stream_control = client.prepare_stream_control(
                        probe_timeout=probe_budget,
                        delete_timeout=2.0,
                    )
                except Exception as exc:
                    raise _classify_exception(exc, connection=resolved_connection) from exc
                if probe.support == StreamControlSupport.SUPPORTED:
                    if stream_control is None:
                        raise _error(
                            ErrorCategory.PROTOCOL,
                            "supported stream control probe returned no exact control",
                            connection=resolved_connection,
                            fallback="stream control protocol mismatch",
                        )
                    if live is not None:
                        exact_stream_control = stream_control

                        def cancel_generation() -> bool:
                            # The registry sets its stable cancellation token
                            # before invoking this callback. Capture only the
                            # exact upstream control so terminal live-state
                            # cleanup cannot race this DELETE through `live`.
                            return bool(exact_stream_control.delete())

                        live.set_cancel(CancelScope.GENERATION, cancel_generation)
                else:
                    stream_control = None
                    if live is not None:
                        reason = probe.reason or probe.support.value
                        warnings.append(f"generation-scoped cancellation unavailable: {reason}")
                if live is not None:
                    live.set_phase("loading", model=exact_model)

                check_caption_release()
                stream_budget = remaining(
                    require_positive=True,
                    stage="prompt submission",
                )
                stream_result = client.stream_chat(
                    payload,
                    timeout=stream_budget,
                    chunk_timeout=min(60.0, stream_budget),
                    on_update=on_update,
                    cancel=cancelled,
                    stream_control=stream_control,
                )

            def validate_stream() -> None:
                nonlocal primary, stream_failure
                if stream_result is None:  # pragma: no cover - defensive invariant
                    primary = _error(
                        ErrorCategory.PROTOCOL,
                        "stream returned no result",
                        connection=resolved_connection,
                        fallback="stream returned no result",
                    )
                else:
                    stream_failure = (
                        None
                        if stream_result.success
                        else _stream_error(
                            stream_result,
                            resolved_connection,
                            image_unsupported_message=image_unsupported_message,
                        )
                    )
                    partial_policy = PartialOutputPolicy(partial_output_policy)
                    if stream_failure is not None and not (
                        stream_result.partial
                        and partial_policy == PartialOutputPolicy.RETURN_MARKED_PARTIAL
                        and _caption_spec is None
                    ):
                        if cleanup_warning is not None:
                            info = stream_failure.info
                            primary = CanonicalGenerationError(
                                GenerationErrorInfo(
                                    category=info.category,
                                    message=f"{info.message}; {cleanup_warning}"[:4096],
                                    status_code=info.status_code,
                                    retryable=info.retryable,
                                )
                            )
                        else:
                            primary = stream_failure
                    elif request.structured_output_kind in {"json_object", "json_schema"}:
                        try:
                            parsed_json = json.loads(
                                stream_result.response,
                                parse_constant=_reject_json_constant,
                            )
                            if request.structured_output_kind == "json_object" and not isinstance(
                                parsed_json, Mapping
                            ):
                                raise ValueError("JSON object response must be an object")
                        except (json.JSONDecodeError, ValueError) as exc:
                            primary = _error(
                                ErrorCategory.STRUCTURED_JSON,
                                exc,
                                connection=resolved_connection,
                                fallback="structured response is not valid JSON",
                            )
                    if (
                        primary is None
                        and managed
                        and router_mode
                        and stream_result.model
                        and stream_result.model != exact_model
                    ):
                        primary = _error(
                            ErrorCategory.PROTOCOL,
                            (
                                f"router returned model {stream_result.model!r} "
                                f"for requested exact model {exact_model!r}"
                            ),
                            connection=resolved_connection,
                            fallback="router response model mismatch",
                        )

                    if primary is None and prepared_audio is not None:
                        try:
                            if stream_result.finish_reason == "length":
                                raise AudioResponseError(
                                    "transcription reached its 128-token limit; no transcript was accepted"
                                )
                            parsed_audio = parse_asr_response(stream_result.response)
                            warnings.extend(parsed_audio.warnings)
                        except Exception as exc:
                            primary = _classify_exception(exc, connection=resolved_connection)

            def make_result(
                item_release: GenerationReleaseInfo, item_release_seconds: float | None
            ) -> GenerationResult:
                state = (
                    GenerationState.COMPLETE
                    if stream_result.success
                    else (
                        GenerationState.CANCELLED
                        if stream_result.cancelled
                        else GenerationState.PARTIAL
                    )
                )
                timing = GenerationTiming(
                    first_chunk_seconds=(
                        first_update - generation_started if first_update is not None else None
                    ),
                    generation_seconds=generation_finished - generation_started,
                    release_seconds=item_release_seconds,
                    total_seconds=self.clock() - started,
                )
                try:
                    result = GenerationResult(
                        state=state,
                        response=stream_result.response,
                        thinking=stream_result.thinking,
                        requested_model=selected_model or None,
                        effective_model=stream_result.model or exact_model,
                        profile_id=profile.profile_id,
                        profile_sha256=profile.content_sha256,
                        seed=request.seed,
                        image_count=request.image_count,
                        structured_output_kind=request.structured_output_kind,
                        usage=GenerationUsage.from_mapping(stream_result.usage),
                        finish_reason=stream_result.finish_reason,
                        response_id=stream_result.response_id,
                        chunks=stream_result.chunks,
                        terminal=(
                            stream_result.done_received or stream_result.finish_reason is not None
                        ),
                        done_received=stream_result.done_received,
                        timing=timing,
                        release=item_release,
                        error=stream_failure.info if stream_failure is not None else None,
                        warnings=tuple(warnings),
                        messages_sha256=messages.content_sha256 if messages is not None else None,
                        message_count=len(messages.messages) if messages is not None else 0,
                        operation=request.operation,
                        budget=request_budget,
                        budget_payload_sha256=budget_payload_sha256,
                    )
                except Exception as exc:
                    raise _error(
                        ErrorCategory.PROTOCOL,
                        exc,
                        connection=resolved_connection,
                        fallback="invalid normalized stream result",
                    ) from exc
                return result

            try:
                if managed:
                    try:
                        exact_model, lease = runtime_service.resolve_and_begin_generation(
                            requested_model=exact_model,
                            resolve_router_model=resolve_router_model,
                            release_after=release_after_generation,
                            source="canonical_generate",
                        )
                    except CanonicalGenerationError:
                        raise
                    except (RouterIdentityError, ValueError) as exc:
                        raise _error(
                            ErrorCategory.MODEL_MISSING,
                            exc,
                            connection=resolved_connection,
                            fallback="router model was not found",
                        ) from exc
                    except DeadlineExceeded as exc:
                        raise _classify_exception(exc, connection=resolved_connection) from exc
                    except Exception as exc:
                        raise _error(
                            ErrorCategory.RUNTIME_UNAVAILABLE,
                            exc,
                            connection=resolved_connection,
                            fallback="managed runtime changed before generation admission",
                        ) from exc

                    lease_mode = str(getattr(getattr(lease, "mode", None), "value", ""))
                    router_mode = lease_mode == "router"
                    try:
                        current_connection, still_managed = self.manager.connection_for(
                            endpoint,
                            api_key_env=resolved_api_key_env,
                            verify_tls=resolved_verify_tls,
                            request_timeout=resolved_timeout,
                        )
                    except Exception as exc:
                        raise _error(
                            ErrorCategory.RUNTIME_UNAVAILABLE,
                            exc,
                            connection=resolved_connection,
                            fallback="managed connection changed after generation admission",
                        ) from exc
                    if not still_managed:
                        raise _error(
                            ErrorCategory.RUNTIME_UNAVAILABLE,
                            "managed connection ownership changed after generation admission",
                            connection=resolved_connection,
                            fallback="managed connection ownership changed",
                        )
                    resolved_connection = current_connection
                    _validate_canonical_transport(
                        resolved_connection,
                        api_key_env=resolved_api_key_env,
                    )

                request = replace(request, requested_model=exact_model)
                payload = build_canonical_payload(
                    request,
                    images=normalized_images,
                    structured_output=constraint,
                    token_ban=normalized_token_ban,
                    audio=prepared_audio,
                    include_usage=(
                        _budget_only or budget_policy != "off" or prepared_audio is not None
                    ),
                )
                client = self.client_factory(resolved_connection)
                if prepared_audio is not None:
                    if not managed or router_mode:
                        raise UnsupportedAudioModelError(
                            "transcription requires an owned direct ASR runtime"
                        )
                    config = self.manager.current_config
                    model_path = getattr(config, "model_path", None)
                    projector_path = getattr(config, "mmproj_path", None)
                    if not model_path or not projector_path:
                        raise UnsupportedAudioModelError(
                            "select the supported ASR model and audio projector explicitly"
                        )
                    verify_approved_pair(
                        model_path,
                        projector_path,
                        supported_pair=_supported_pair,
                        cancel_check=cancelled,
                        deadline_check=lambda: remaining(
                            require_positive=True, stage="ASR identity verification"
                        ),
                    )
                    props = client.passive_props(
                        timeout=remaining(require_positive=True, stage="audio capability probe")
                    )
                    if getattr(props, "modalities", {}).get("audio") is not True:
                        raise UnsupportedAudioModelError(
                            "the selected ASR runtime must explicitly advertise audio support"
                        )
                if managed and not router_mode:
                    try:
                        projector_status = getattr(self.manager, "projector_status", None)
                    except Exception:
                        projector_status = None
                    if (
                        isinstance(projector_status, Mapping)
                        and projector_status.get("mode") == "none"
                    ):
                        image_unsupported_message = _EXPLICIT_TEXT_ONLY_IMAGE_MESSAGE
                if normalized_images:
                    capability_budget = min(
                        _IMAGE_CAPABILITY_PROBE_SECONDS,
                        remaining(require_positive=True, stage="image capability probe"),
                    )
                    props_model = exact_model if router_mode or not managed else None
                    try:
                        props = client.passive_props(
                            props_model,
                            timeout=capability_budget,
                        )
                    except LlamaClientError:
                        props = None
                    modalities = getattr(props, "modalities", None)
                    if isinstance(modalities, Mapping) and modalities.get("vision") is False:
                        raise _error(
                            ErrorCategory.CAPABILITY_UNSUPPORTED,
                            image_unsupported_message,
                            connection=resolved_connection,
                            fallback=_VISION_UNAVAILABLE_MESSAGE,
                        )

                if _budget_only or budget_policy != "off":
                    if cancelled():
                        raise OperationCancelled("request budget cancelled")
                    budget_warnings = []
                    try:
                        props = client.passive_props(
                            exact_model if router_mode or not managed else None,
                            timeout=remaining(require_positive=True, stage="context lookup"),
                            cancel=cancelled,
                        )
                    except LlamaClientError as exc:
                        if exc.status_code not in {404, 405}:
                            raise
                        props = None
                        budget_warnings.append("effective per-slot context is unavailable")
                    context_limit = effective_context_limit(getattr(props, "raw", {}))
                    count = client.count_chat_input_tokens(
                        payload,
                        autoload=False,
                        timeout=remaining(require_positive=True, stage="request token count"),
                        cancel=cancelled,
                    )
                    if count.input_tokens is None:
                        budget_warnings.append(
                            "the server does not expose complete-request token counting"
                        )
                    request_budget = RequestBudget(
                        input_tokens=count.input_tokens,
                        context_limit=context_limit,
                        max_tokens=request.max_tokens,
                        model=exact_model
                        or getattr(props, "model_alias", None)
                        or getattr(props, "model_path", None),
                        input_source=INPUT_TOKEN_SOURCE if count.input_tokens is not None else None,
                        context_source=CONTEXT_LIMIT_SOURCE if context_limit is not None else None,
                        warnings=tuple(budget_warnings),
                    )
                    budget_payload_sha256 = sha256(
                        json.dumps(
                            payload,
                            ensure_ascii=False,
                            allow_nan=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest()
                    enforce_request_budget(
                        request_budget, policy=budget_policy if budget_policy != "off" else "report"
                    )
                    if cancelled():
                        raise OperationCancelled("request budget cancelled")

                if _caption_spec is not None:
                    for item_index, (item_request, item_image) in enumerate(
                        zip(caption_requests, caption_images, strict=True)
                    ):
                        request = replace(item_request, requested_model=exact_model)
                        normalized_images = (item_image,)
                        payload = build_canonical_payload(
                            request,
                            images=normalized_images,
                            structured_output=constraint,
                            token_ban=normalized_token_ban,
                            include_usage=True,
                        )
                        stream_result = None
                        stream_control = None
                        stream_failure = None
                        stream_cleanup_snapshot = None
                        cleanup_warning = None
                        first_update = None
                        generation_started = self.clock()
                        if live is not None:
                            live.update(
                                response="",
                                thinking="",
                                prompt_progress=None,
                                finish_reason=None,
                                usage=None,
                                error=None,
                                chunks=0,
                                stream_cleanup=None,
                                force_emit=True,
                            )
                        try:
                            check_operation()
                            run_stream(payload)
                            generation_finished = self.clock()
                            if stream_result is not None:
                                cleanup_warning = _stream_cleanup_warning(stream_result)
                                if cleanup_warning is not None:
                                    warnings.append(cleanup_warning)
                            validate_stream()
                            if primary is not None:
                                raise primary
                            if not stream_result.response.strip():
                                raise _error(
                                    ErrorCategory.PROTOCOL,
                                    "caption response is empty",
                                    fallback="caption response is empty",
                                )
                            item_result = make_result(GenerationReleaseInfo(), None)
                            row = caption_rows[item_index]
                            row.update(
                                state="complete",
                                response=item_result.response,
                                thinking=item_result.thinking,
                                result=item_result.as_dict(),
                            )
                            # Stop before the next request if accumulated evidence would exceed
                            # the final bound. Reserve room for error and terminal release metadata.
                            if (
                                len(json.dumps(caption_rows, ensure_ascii=False).encode("utf-8"))
                                > MAX_CAPTION_RESULT_BYTES - 65536
                            ):
                                row.update(
                                    state="not_attempted", response="", thinking="", result=None
                                )
                                raise ValueError("caption results exceed the group result limit")
                        except Exception as exc:
                            caption_failure = _classify_exception(
                                exc, connection=resolved_connection
                            )
                            caption_rows[item_index].update(
                                state="failed",
                                response="",
                                thinking="",
                                result=None,
                                error={
                                    "category": caption_failure.category.value,
                                    "message": caption_failure.info.message,
                                },
                            )
                            caption_state = (
                                "cancelled"
                                if caption_failure.category == ErrorCategory.CANCELLED
                                else "partial"
                                if item_index
                                else "failed"
                            )
                            if (
                                PartialOutputPolicy(partial_output_policy)
                                == PartialOutputPolicy.RAISE_ERROR
                            ):
                                raise caption_failure from None
                            primary = None
                            break
                        finally:
                            caption_progress(item_index + 1)
                            if live is not None:
                                # The group token remains stable; an ended item's DELETE must
                                # never be used as the cancellation target between requests.
                                live.set_cancel(
                                    CancelScope.PROMPT
                                    if identity and identity.prompt_id
                                    else CancelScope.NONE
                                )
                elif not _budget_only:
                    run_stream(payload)
            except BaseException as exc:
                primary = exc
            finally:
                generation_finished = self.clock()
                cleanup = getattr(stream_control, "cleanup", None)
                snapshot = getattr(cleanup, "snapshot", None)
                if callable(snapshot):
                    try:
                        stream_cleanup_snapshot = snapshot()
                    except Exception:
                        stream_cleanup_snapshot = None
                if stream_cleanup_snapshot is None and stream_result is not None:
                    stream_cleanup_snapshot = stream_result.stream_cleanup
                if lease is not None:
                    try:
                        release_handle = runtime_service.finish_generation(lease)
                        if release_handle is not None:
                            release_info = _accepted_release_info(
                                release_handle,
                                router=router_mode,
                                target_model=exact_model,
                            )
                    except Exception as exc:
                        release_wait_error = exc

            if primary is not None and not isinstance(primary, Exception):
                if live is not None:
                    live.finish(
                        phase="cancelled",
                        result=stream_result,
                        release=release_info.as_dict(),
                        stream_cleanup=stream_cleanup_snapshot,
                        error={
                            "category": ErrorCategory.CANCELLED.value,
                            "message": "Comfy job interrupted",
                        },
                    )
                    live = None
                raise primary.with_traceback(primary.__traceback__)

            if stream_result is not None and _caption_spec is None:
                cleanup_warning = _stream_cleanup_warning(stream_result)
                if cleanup_warning is not None:
                    warnings.append(cleanup_warning)

            if primary is None and not _budget_only and _caption_spec is None:
                validate_stream()

            release_seconds: float | None = None
            if (
                lease is not None
                and release_after_generation
                and release_handle is None
                and release_wait_error is None
            ):
                release_wait_error = RuntimeError(
                    "runtime did not return the accepted terminal release handle"
                )
                release_info = _failed_release_info(
                    release_wait_error,
                    status="handle_missing",
                    connection=resolved_connection,
                    router=router_mode,
                    target_model=exact_model,
                )
            if release_handle is not None and release_wait_error is None:
                if live is not None:
                    # Upstream generation is over. Exact DELETE control is no
                    # longer truthful while terminal runtime cleanup is pending.
                    live.set_cancel(CancelScope.NONE)
                    live.set_phase("releasing")
                release_started = self.clock()
                try:
                    release_result = release_handle.wait(
                        timeout=remaining(
                            require_positive=False,
                            stage="terminal runtime release",
                        ),
                        cancel=cancel_check,
                    )
                    release_seconds = self.clock() - release_started
                    release_info = _release_info(
                        release_result,
                        elapsed_seconds=release_seconds,
                        connection=resolved_connection,
                    )
                    if not release_info.success:
                        release_wait_error = RuntimeError(
                            release_info.error.message
                            if release_info.error
                            else "release did not reach terminal success"
                        )
                except Exception as exc:
                    release_seconds = self.clock() - release_started
                    release_wait_error = exc
                    release_info = _failed_release_info(
                        exc,
                        status="wait_failed",
                        connection=resolved_connection,
                        router=router_mode,
                        target_model=exact_model,
                        elapsed_seconds=release_seconds,
                    )

            if release_wait_error is not None and release_info.policy == ReleasePolicy.REUSE:
                release_info = _failed_release_info(
                    release_wait_error,
                    status="finish_failed",
                    connection=resolved_connection,
                    router=router_mode,
                    target_model=exact_model,
                    policy=(
                        ReleasePolicy.RELEASE_AFTER_GENERATION
                        if release_after_generation
                        else ReleasePolicy.REUSE
                    ),
                )

            if primary is not None:
                if release_wait_error is not None:
                    raise _with_release_failure(
                        primary,
                        release_wait_error,
                        connection=resolved_connection,
                    )
                raise primary.with_traceback(primary.__traceback__)
            if release_wait_error is not None:
                release_error: object = release_wait_error
                if cleanup_warning is not None:
                    release_error = f"{release_wait_error}; {cleanup_warning}"
                raise _error(
                    ErrorCategory.RELEASE_FAILED,
                    release_error,
                    connection=resolved_connection,
                    fallback="terminal runtime release failed",
                )
            if _caption_spec is not None:
                outputs = caption_batch_outputs(
                    _caption_spec,
                    caption_rows,
                    state=caption_state,
                    release=release_info.as_dict(),
                    warnings=tuple(dict.fromkeys(warnings)),
                )
                if live is not None:
                    live.finish(
                        phase="complete"
                        if caption_state == "complete"
                        else "cancelled"
                        if caption_state == "cancelled"
                        else "failed",
                        result=stream_result,
                        release=release_info.as_dict(),
                        stream_cleanup=stream_cleanup_snapshot,
                        error=caption_failure.info.as_dict() if caption_failure else None,
                    )
                    live = None
                return outputs

            if _budget_only:
                assert request_budget is not None
                observation = {
                    "schema_version": 1,
                    "budget": request_budget.as_dict(),
                    "payload_sha256": budget_payload_sha256,
                    "runtime_epoch": getattr(lease, "runtime_epoch", None),
                    "release": release_info.as_dict(),
                    "elapsed_seconds": self.clock() - started,
                }
                if live is not None:
                    live.finish(phase="complete", release=release_info.as_dict())
                    live = None
                return observation

            assert stream_result is not None

            result = make_result(release_info, release_seconds)
            error_info = result.error
            if live is not None:
                live.finish(
                    phase=(
                        "complete"
                        if result.state == GenerationState.COMPLETE
                        else (
                            "cancelled" if result.state == GenerationState.CANCELLED else "failed"
                        )
                    ),
                    result=stream_result,
                    release=release_info.as_dict(),
                    stream_cleanup=stream_cleanup_snapshot,
                    error=error_info.as_dict() if error_info else None,
                )
                live = None
            return result.response, result.thinking, result
        except CanonicalGenerationError as exc:
            public_error = CanonicalGenerationError(exc.info)
            if live is not None:
                live.finish(
                    phase=("cancelled" if exc.category == ErrorCategory.CANCELLED else "failed"),
                    result=stream_result,
                    release=release_info.as_dict(),
                    stream_cleanup=stream_cleanup_snapshot,
                    error=exc.info.as_dict(),
                )
                live = None
            raise public_error from None
        except Exception as exc:
            normalized = _classify_exception(exc, connection=resolved_connection)
            if live is not None:
                live.finish(
                    phase=(
                        "cancelled" if normalized.category == ErrorCategory.CANCELLED else "failed"
                    ),
                    result=stream_result,
                    release=release_info.as_dict(),
                    stream_cleanup=stream_cleanup_snapshot,
                    error=normalized.info.as_dict(),
                )
                live = None
            raise CanonicalGenerationError(normalized.info) from None
        except BaseException:
            if live is not None:
                live.finish(
                    phase="cancelled",
                    result=stream_result,
                    release=release_info.as_dict(),
                    stream_cleanup=stream_cleanup_snapshot,
                    error={
                        "category": ErrorCategory.CANCELLED.value,
                        "message": "Comfy job interrupted",
                    },
                )
                live = None
            raise
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass


__all__ = [
    "CanonicalGenerationError",
    "CanonicalGenerationExecutor",
    "RUNNING_MODEL",
    "build_canonical_payload",
]
