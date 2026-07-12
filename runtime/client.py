"""Typed client contracts for current llama-server HTTP APIs."""

from __future__ import annotations

import json
import math
import re
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

import requests

if TYPE_CHECKING:
    from .streaming import ModelEvent, StreamResult, StreamUpdate

Clock = Callable[[], float]
CancelCheck = Callable[[], bool]
VerifyValue = bool | str
CertValue = str | tuple[str, str]

STREAM_CONTROL_CACHE_SECONDS = 60.0
STREAM_CONTROL_CACHE_MAX_ENTRIES = 128
STREAM_CONTROL_DELETE_TIMEOUT = 2.0
JSON_RESPONSE_MAX_BYTES = 16 * 1024 * 1024
ERROR_RESPONSE_MAX_BYTES = 64 * 1024
RESPONSE_READ_CHUNK_BYTES = 64 * 1024


class LlamaClientError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        endpoint: str | None = None,
        body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.endpoint = endpoint
        self.body = body


class ResponseBodyLimitError(LlamaClientError):
    """A response exceeded the amount of data this client will materialize."""


class ResponseProtocolError(LlamaClientError):
    """A response body could not be decoded using the advertised JSON contract."""


class DeadlineExceeded(LlamaClientError, TimeoutError):
    pass


class OperationCancelled(LlamaClientError):
    pass


class ModelNotFoundError(LlamaClientError):
    pass


class AmbiguousModelError(LlamaClientError):
    pass


class ModelOperationError(LlamaClientError):
    def __init__(self, message: str, *, model: RouterModel | None = None) -> None:
        super().__init__(message)
        self.model = model


def redact_secrets(value: object, secrets: Iterable[str | None] = ()) -> str:
    text = str(value)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "<redacted>")
    text = re.sub(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+", r"\1<redacted>", text)
    text = re.sub(r"(?i)(x-api-key\s*[:=]\s*)[^\s,;]+", r"\1<redacted>", text)
    return text


def _read_bounded_response_bytes(
    response: Any,
    max_bytes: int,
    *,
    truncate: bool = False,
    check: Callable[[], None] | None = None,
) -> tuple[bytes, bool]:
    """Read at most ``max_bytes`` without touching eager ``text``/``json`` properties."""

    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("response body limit must be a positive integer")

    body = bytearray()
    for chunk in response.iter_content(chunk_size=RESPONSE_READ_CHUNK_BYTES):
        if check is not None:
            check()
        if not chunk:
            continue
        if isinstance(chunk, str):
            try:
                encoded = chunk.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ResponseProtocolError("response body was not valid UTF-8") from exc
        elif isinstance(chunk, (bytes, bytearray, memoryview)):
            encoded = bytes(chunk)
        else:
            raise ResponseProtocolError("response body contained an invalid byte chunk")

        remaining = max_bytes - len(body)
        if len(encoded) > remaining:
            if truncate:
                body.extend(encoded[:remaining])
                return bytes(body), True
            raise ResponseBodyLimitError("response body exceeded the configured safety limit")
        body.extend(encoded)
    if check is not None:
        check()
    return bytes(body), False


@dataclass(frozen=True, slots=True)
class AuthConfig:
    api_key: str | None = field(default=None, repr=False)
    header: str = "Authorization"

    def __post_init__(self) -> None:
        if self.api_key is not None and not isinstance(self.api_key, str):
            raise TypeError("api_key must be a string or None")
        if self.header not in {"Authorization", "X-Api-Key"}:
            raise ValueError("auth header must be 'Authorization' or 'X-Api-Key'")
        if self.api_key is not None and not self.api_key:
            object.__setattr__(self, "api_key", None)

    def as_headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        value = f"Bearer {self.api_key}" if self.header == "Authorization" else self.api_key
        return {self.header: value}

    @property
    def fingerprint(self) -> str | None:
        return sha256(self.api_key.encode("utf-8")).hexdigest() if self.api_key else None


@dataclass(frozen=True, slots=True)
class TLSConfig:
    verify: VerifyValue = True
    cert: CertValue | None = None

    def __post_init__(self) -> None:
        if isinstance(self.cert, list):
            object.__setattr__(self, "cert", tuple(self.cert))
        if isinstance(self.cert, tuple) and len(self.cert) != 2:
            raise ValueError("TLS client certificate tuple must contain certificate and key paths")


@dataclass(frozen=True, slots=True)
class ConnectionConfig:
    base_url: str = "http://127.0.0.1:8080"
    auth: AuthConfig = field(default_factory=AuthConfig)
    tls: TLSConfig = field(default_factory=TLSConfig)
    connect_timeout: float = 10.0
    read_timeout: float = 60.0
    default_deadline: float | None = 300.0
    default_headers: tuple[tuple[str, str], ...] = field(default_factory=tuple, repr=False)

    def __post_init__(self) -> None:
        parsed = urlsplit(self.base_url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must be an absolute http or https URL")
        if parsed.username or parsed.password:
            raise ValueError("base_url must not contain credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("base_url must not contain a query or fragment")
        try:
            hostname = parsed.hostname
            _port = parsed.port
        except ValueError as exc:
            raise ValueError(f"base_url has an invalid host or port: {exc}") from exc
        if not hostname or hostname == "*" or any(character.isspace() for character in hostname):
            raise ValueError(
                "base_url must contain a concrete host; IPv6 literals must use brackets"
            )
        normalized_path = parsed.path.rstrip("/")
        normalized = urlunsplit((parsed.scheme.lower(), parsed.netloc, normalized_path, "", ""))
        object.__setattr__(self, "base_url", normalized)
        headers = tuple((str(key), str(value)) for key, value in self.default_headers)
        object.__setattr__(self, "default_headers", headers)
        if (
            self.connect_timeout <= 0
            or self.read_timeout <= 0
            or not math.isfinite(self.connect_timeout)
            or not math.isfinite(self.read_timeout)
        ):
            raise ValueError("connection and read timeouts must be positive")
        if self.default_deadline is not None and (
            self.default_deadline <= 0 or not math.isfinite(self.default_deadline)
        ):
            raise ValueError("default_deadline must be positive or None")

    def url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def request_headers(self, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        headers = dict(self.default_headers)
        if extra:
            headers.update(extra)
        # Managed auth wins over accidentally supplied default or per-request values.
        if self.auth.api_key:
            for key in tuple(headers):
                if key.lower() in {"authorization", "x-api-key"}:
                    headers.pop(key)
        headers.update(self.auth.as_headers())
        return headers

    @property
    def secrets(self) -> tuple[str, ...]:
        secrets: list[str] = [self.auth.api_key] if self.auth.api_key else []
        for key, value in self.default_headers:
            if key.lower() not in {"authorization", "x-api-key"}:
                continue
            secrets.append(value)
            if value.lower().startswith("bearer "):
                secrets.append(value[7:])
        return tuple(secrets)

    @property
    def fingerprint(self) -> str:
        payload = {
            "base_url": self.base_url,
            "auth": self.auth.fingerprint,
            "auth_header": self.auth.header,
            "tls_verify": self.tls.verify,
            "tls_cert": self.tls.cert,
            "connect_timeout": self.connect_timeout,
            "read_timeout": self.read_timeout,
            "default_deadline": self.default_deadline,
            "default_headers": sorted(dict(self.default_headers).items()),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Deadline:
    timeout: float | None
    _clock: Clock = field(default=time.monotonic, repr=False, compare=False)
    _started: float = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.timeout is not None and (self.timeout < 0 or not math.isfinite(self.timeout)):
            raise ValueError("deadline timeout must be non-negative or None")
        object.__setattr__(self, "_started", self._clock())

    @property
    def elapsed(self) -> float:
        return max(0.0, self._clock() - self._started)

    @property
    def remaining(self) -> float | None:
        if self.timeout is None:
            return None
        return max(0.0, self.timeout - self.elapsed)

    @property
    def expired(self) -> bool:
        remaining = self.remaining
        return remaining is not None and remaining <= 0

    def raise_if_expired(self, operation: str = "operation") -> None:
        if self.expired:
            raise DeadlineExceeded(f"{operation} exceeded its {self.timeout:g}s deadline")

    def request_timeout(self, connect_timeout: float, read_timeout: float) -> tuple[float, float]:
        self.raise_if_expired("request")
        remaining = self.remaining
        if remaining is None:
            return (connect_timeout, read_timeout)
        # requests rejects a zero timeout; expiration is checked immediately above.
        cap = max(0.001, remaining)
        return (min(connect_timeout, cap), min(read_timeout, cap))


class ModelState(str, Enum):
    UNLOADED = "unloaded"
    LOADING = "loading"
    LOADED = "loaded"
    SLEEPING = "sleeping"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    FAILED = "failed"
    UNKNOWN = "unknown"

    @classmethod
    def from_upstream(cls, value: object, *, failed: bool = False) -> ModelState:
        normalized = str(value or "unknown").lower()
        if failed and normalized == "unloaded":
            return cls.FAILED
        try:
            return cls(normalized)
        except ValueError:
            return cls.UNKNOWN


@dataclass(frozen=True, slots=True)
class HealthStatus:
    ok: bool
    status_code: int
    status: str | None
    message: str | None
    raw: Any


@dataclass(frozen=True, slots=True)
class ServerProps:
    role: str | None
    build_info: str | None
    model_path: str | None
    model_alias: str | None
    is_sleeping: bool
    modalities: Mapping[str, bool]
    raw: Mapping[str, Any]

    @property
    def is_router(self) -> bool:
        return self.role == "router"


@dataclass(frozen=True, slots=True)
class RouterModel:
    id: str
    state: ModelState
    aliases: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    failed: bool = False
    exit_code: int | None = None
    progress: Any = None
    input_modalities: tuple[str, ...] = ()
    output_modalities: tuple[str, ...] = ()
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def callable(self) -> bool:
        return self.state in {ModelState.LOADED, ModelState.SLEEPING}

    @property
    def resident(self) -> bool:
        return self.state in {ModelState.LOADING, ModelState.LOADED}

    @property
    def unloaded(self) -> bool:
        return self.state in {ModelState.UNLOADED, ModelState.FAILED}


@dataclass(frozen=True, slots=True)
class TokenizeResult:
    tokens: tuple[Any, ...]
    raw: Mapping[str, Any]

    @property
    def count(self) -> int:
        return len(self.tokens)


@dataclass(frozen=True, slots=True)
class ModelOperationResult:
    model: RouterModel
    accepted: bool
    completed: bool
    elapsed: float

    @property
    def success(self) -> bool:
        return self.completed

    @property
    def failed_model(self) -> bool:
        return self.model.state == ModelState.FAILED


class StreamControlSupport(str, Enum):
    """Capability state for llama.cpp's optional resumable stream interface."""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class StreamControlProbe:
    support: StreamControlSupport
    reason: str | None = None

    @property
    def supported(self) -> bool:
        return self.support == StreamControlSupport.SUPPORTED


StreamDeleteCallback = Callable[[uuid.UUID, float], bool]


@dataclass(frozen=True, slots=True)
class StreamCleanupSnapshot:
    attempts: int
    confirmed: bool
    failures: int
    last_error_type: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempts": self.attempts,
            "confirmed": self.confirmed,
            "failures": self.failures,
            "last_error_type": self.last_error_type,
        }


class StreamCleanupOutcome:
    """Thread-safe, redacted visibility into idempotent stream DELETE attempts."""

    __slots__ = ("_attempts", "_confirmed", "_failures", "_last_error_type", "_lock")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._attempts = 0
        self._confirmed = False
        self._failures = 0
        self._last_error_type: str | None = None

    def _begin(self) -> None:
        with self._lock:
            self._attempts += 1

    def _complete(self, confirmed: bool) -> None:
        with self._lock:
            self._confirmed = self._confirmed or confirmed

    def _fail(self, error: BaseException) -> None:
        with self._lock:
            self._failures += 1
            self._last_error_type = type(error).__name__[:128]

    @property
    def attempts(self) -> int:
        with self._lock:
            return self._attempts

    @property
    def confirmed(self) -> bool:
        with self._lock:
            return self._confirmed

    @property
    def failures(self) -> int:
        with self._lock:
            return self._failures

    @property
    def last_error_type(self) -> str | None:
        with self._lock:
            return self._last_error_type

    def as_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "attempts": self._attempts,
                "confirmed": self._confirmed,
                "failures": self._failures,
                "last_error_type": self._last_error_type,
            }

    def snapshot(self) -> StreamCleanupSnapshot:
        with self._lock:
            return StreamCleanupSnapshot(
                attempts=self._attempts,
                confirmed=self._confirmed,
                failures=self._failures,
                last_error_type=self._last_error_type,
            )

    def __repr__(self) -> str:
        values = self.as_dict()
        return (
            "StreamCleanupOutcome("
            f"attempts={values['attempts']}, "
            f"confirmed={values['confirmed']}, "
            f"failures={values['failures']}, "
            f"last_error_type={values['last_error_type']!r})"
        )


@dataclass(frozen=True, slots=True)
class StreamControl:
    """One internally identified upstream replay session.

    The connection is deliberately private and excluded from ``repr`` because it
    can contain resolved authentication material.  Every call to :meth:`delete`
    performs the exact idempotent upstream DELETE using a fresh HTTP client, so a
    browser cancellation route can run concurrently with the generation stream.
    """

    connection: ConnectionConfig = field(repr=False, compare=False)
    conversation_id: uuid.UUID
    delete_timeout: float = STREAM_CONTROL_DELETE_TIMEOUT
    _delete: StreamDeleteCallback | None = field(default=None, repr=False, compare=False)
    cleanup: StreamCleanupOutcome = field(
        default_factory=StreamCleanupOutcome,
        init=False,
        repr=False,
        compare=False,
    )
    _delete_lock: threading.Lock = field(
        default_factory=threading.Lock,
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if not isinstance(self.conversation_id, uuid.UUID):
            raise TypeError("conversation_id must be a UUID")
        if self.conversation_id.version != 4:
            raise ValueError("conversation_id must be an internally generated UUID4")
        if self.delete_timeout <= 0 or not math.isfinite(self.delete_timeout):
            raise ValueError("stream control delete timeout must be positive")

    def delete(self) -> bool:
        with self._delete_lock:
            self.cleanup._begin()
            try:
                if self._delete is not None:
                    confirmed = bool(self._delete(self.conversation_id, self.delete_timeout))
                else:
                    with LlamaServerClient(self.connection) as client:
                        confirmed = client.delete_stream(
                            self.conversation_id,
                            timeout=self.delete_timeout,
                        )
            except BaseException as exc:
                self.cleanup._fail(exc)
                raise
            self.cleanup._complete(confirmed)
            return confirmed


_STREAM_CONTROL_CACHE: dict[str, tuple[float, StreamControlProbe]] = {}
_STREAM_CONTROL_CACHE_LOCK = threading.Lock()


def clear_stream_control_cache() -> None:
    """Clear the short-lived endpoint capability cache, primarily for tests."""

    with _STREAM_CONTROL_CACHE_LOCK:
        _STREAM_CONTROL_CACHE.clear()


def parse_router_model(data: Mapping[str, Any]) -> RouterModel:
    model_id = data.get("id") or data.get("model") or data.get("name")
    if not isinstance(model_id, str) or not model_id:
        raise LlamaClientError("router model entry is missing a non-empty id")

    status_raw = data.get("status", data.get("state", "unknown"))
    if isinstance(status_raw, Mapping):
        status_value = status_raw.get("value", "unknown")
        failed = bool(status_raw.get("failed", False))
        exit_code = status_raw.get("exit_code")
        progress = status_raw.get("progress", data.get("progress"))
    else:
        status_value = status_raw
        failed = bool(data.get("failed", False))
        exit_code = data.get("exit_code")
        progress = data.get("progress")

    architecture = data.get("architecture")
    architecture = architecture if isinstance(architecture, Mapping) else {}

    def strings(value: object) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple, set)):
            return ()
        return tuple(item for item in value if isinstance(item, str))

    return RouterModel(
        id=model_id,
        state=ModelState.from_upstream(status_value, failed=failed),
        aliases=strings(data.get("aliases")),
        tags=strings(data.get("tags")),
        failed=failed,
        exit_code=exit_code if isinstance(exit_code, int) else None,
        progress=progress,
        input_modalities=strings(architecture.get("input_modalities")),
        output_modalities=strings(architecture.get("output_modalities")),
        raw=dict(data),
    )


class LlamaServerClient:
    """A current llama-server client with explicit operation barriers."""

    def __init__(
        self,
        connection: ConnectionConfig,
        *,
        session: requests.Session | None = None,
        clock: Clock = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.connection = connection
        self._session = session or requests.Session()
        self._owns_session = session is None
        self._clock = clock
        self._sleeper = sleeper

    def close(self) -> None:
        if self._owns_session:
            self._session.close()

    def __enter__(self) -> LlamaServerClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _deadline(self, deadline: Deadline | None, timeout: float | None) -> Deadline:
        if deadline is not None:
            return deadline
        duration = self.connection.default_deadline if timeout is None else timeout
        return Deadline(duration, self._clock)

    def _response_json(self, response: Any, *, path: str, deadline: Deadline) -> Any:
        try:
            body, _ = _read_bounded_response_bytes(
                response,
                JSON_RESPONSE_MAX_BYTES,
                check=lambda: deadline.raise_if_expired(f"reading {path}"),
            )
        except LlamaClientError as exc:
            if exc.endpoint is None:
                exc.endpoint = path
            raise
        if not body:
            return ""
        try:
            text = body.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ResponseProtocolError(
                "response body was not valid UTF-8",
                endpoint=path,
            ) from exc
        try:
            return json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ResponseProtocolError(
                "response body was not valid JSON",
                endpoint=path,
            ) from exc

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        deadline: Deadline,
        expected: Sequence[int] = (200,),
        params: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> tuple[int, Any, Mapping[str, str]]:
        deadline.raise_if_expired(f"{method} {path}")
        url = self.connection.url(path)
        kwargs: dict[str, Any] = {
            "headers": self.connection.request_headers(),
            "timeout": deadline.request_timeout(
                self.connection.connect_timeout,
                self.connection.read_timeout,
            ),
            "verify": self.connection.tls.verify,
            "stream": True,
            "allow_redirects": False,
        }
        if self.connection.tls.cert is not None:
            kwargs["cert"] = self.connection.tls.cert
        if params:
            kwargs["params"] = dict(params)
        if body is not None:
            kwargs["json"] = dict(body)

        try:
            response = self._session.request(method, url, **kwargs)
        except requests.Timeout as exc:
            raise DeadlineExceeded(
                redact_secrets(f"{method} {path} timed out", self.connection.secrets),
                endpoint=path,
            ) from exc
        except requests.RequestException as exc:
            raise LlamaClientError(
                f"{method} {path} failed",
                endpoint=path,
            ) from exc

        try:
            status_code = int(response.status_code)
            headers = dict(getattr(response, "headers", {}) or {})
            if status_code not in expected:
                try:
                    _read_bounded_response_bytes(
                        response,
                        ERROR_RESPONSE_MAX_BYTES,
                        truncate=True,
                        check=lambda: deadline.raise_if_expired(f"reading {path}"),
                    )
                except (LlamaClientError, requests.RequestException):
                    pass
                raise LlamaClientError(
                    f"HTTP {status_code}: request failed",
                    status_code=status_code,
                    endpoint=path,
                )
            data = self._response_json(response, path=path, deadline=deadline)
            return status_code, data, headers
        except requests.Timeout as exc:
            raise DeadlineExceeded(
                f"{method} {path} timed out",
                endpoint=path,
            ) from exc
        except requests.RequestException as exc:
            raise LlamaClientError(
                f"{method} {path} failed",
                endpoint=path,
            ) from exc
        finally:
            response.close()

    def health(
        self,
        *,
        deadline: Deadline | None = None,
        timeout: float | None = None,
    ) -> HealthStatus:
        active = self._deadline(deadline, timeout)
        status_code, data, _ = self._request_json(
            "GET", "/health", deadline=active, expected=(200, 503)
        )
        status = (
            data.get("status")
            if isinstance(data, Mapping) and isinstance(data.get("status"), str)
            else None
        )
        message: str | None = None
        if isinstance(data, Mapping) and isinstance(data.get("error"), Mapping):
            candidate = data["error"].get("message")
            message = candidate if isinstance(candidate, str) else None
        return HealthStatus(status_code == 200, status_code, status, message, data)

    def props(
        self,
        model: str | None = None,
        *,
        autoload: bool | None = None,
        deadline: Deadline | None = None,
        timeout: float | None = None,
    ) -> ServerProps:
        active = self._deadline(deadline, timeout)
        params: dict[str, Any] = {}
        if model:
            params["model"] = model
        if autoload is not None:
            params["autoload"] = "true" if autoload else "false"
        _, data, _ = self._request_json("GET", "/props", deadline=active, params=params)
        if not isinstance(data, Mapping):
            raise LlamaClientError("/props returned a non-object response", endpoint="/props")
        modalities_raw = data.get("modalities")
        modalities = (
            {str(key): value for key, value in modalities_raw.items() if isinstance(value, bool)}
            if isinstance(modalities_raw, Mapping)
            else {}
        )
        sleeping_raw = data.get("is_sleeping", False)
        return ServerProps(
            role=data.get("role") if isinstance(data.get("role"), str) else None,
            build_info=data.get("build_info") if isinstance(data.get("build_info"), str) else None,
            model_path=data.get("model_path") if isinstance(data.get("model_path"), str) else None,
            model_alias=data.get("model_alias")
            if isinstance(data.get("model_alias"), str)
            else None,
            is_sleeping=sleeping_raw if isinstance(sleeping_raw, bool) else False,
            modalities=modalities,
            raw=dict(data),
        )

    def passive_props(
        self,
        model: str | None = None,
        *,
        deadline: Deadline | None = None,
        timeout: float | None = None,
    ) -> ServerProps:
        """Read props without permitting router autoload side effects."""

        return self.props(
            model,
            autoload=False,
            deadline=deadline,
            timeout=timeout,
        )

    def models(
        self,
        *,
        reload: bool = False,
        deadline: Deadline | None = None,
        timeout: float | None = None,
    ) -> tuple[RouterModel, ...]:
        active = self._deadline(deadline, timeout)
        params = {"reload": "1"} if reload else None
        _, data, _ = self._request_json(
            "GET",
            "/models",
            deadline=active,
            params=params,
        )
        entries = data.get("data") if isinstance(data, Mapping) else data
        if not isinstance(entries, list):
            raise LlamaClientError("/models returned an invalid model list", endpoint="/models")
        parsed: list[RouterModel] = []
        for entry in entries:
            if not isinstance(entry, Mapping):
                raise LlamaClientError(
                    "/models returned a non-object model entry", endpoint="/models"
                )
            parsed.append(parse_router_model(entry))
        return tuple(parsed)

    list_models = models

    @staticmethod
    def find_model(model: str, models: Sequence[RouterModel]) -> RouterModel:
        direct = [entry for entry in models if entry.id == model]
        if len(direct) == 1:
            return direct[0]
        aliases = [entry for entry in models if model in entry.aliases]
        if len(aliases) == 1:
            return aliases[0]
        if len(aliases) > 1:
            raise AmbiguousModelError(f"model alias '{model}' matches multiple router models")
        raise ModelNotFoundError(f"model '{model}' was not returned by /models")

    def tokenize(
        self,
        content: str,
        *,
        model: str | None = None,
        add_special: bool = False,
        parse_special: bool = True,
        with_pieces: bool = False,
        deadline: Deadline | None = None,
        timeout: float | None = None,
    ) -> TokenizeResult:
        active = self._deadline(deadline, timeout)
        body: dict[str, Any] = {
            "content": content,
            "add_special": add_special,
            "parse_special": parse_special,
            "with_pieces": with_pieces,
        }
        if model:
            body["model"] = model
        _, data, _ = self._request_json("POST", "/tokenize", deadline=active, body=body)
        if not isinstance(data, Mapping) or not isinstance(data.get("tokens"), list):
            raise LlamaClientError("/tokenize returned an invalid token list", endpoint="/tokenize")
        return TokenizeResult(tuple(data["tokens"]), dict(data))

    def probe_stream_control(
        self,
        *,
        timeout: float = STREAM_CONTROL_DELETE_TIMEOUT,
        force: bool = False,
    ) -> StreamControlProbe:
        """Probe llama.cpp's optional replay/cancel API without creating a session.

        A well-formed list response is the only positive result.  Current
        llama.cpp returns 404/405 when the internal interface is absent.  Auth
        failures remain real configuration errors; other transport, timeout, or
        server failures are an honest Unknown so generation can use whole-job
        cancellation without mislabelling it as node-local.
        """

        if timeout <= 0 or not math.isfinite(timeout):
            raise ValueError("stream control probe timeout must be positive")

        fingerprint = self.connection.fingerprint
        now = self._clock()
        if not force:
            with _STREAM_CONTROL_CACHE_LOCK:
                expired = [
                    key
                    for key, (expires_at, _) in _STREAM_CONTROL_CACHE.items()
                    if expires_at <= now
                ]
                for key in expired:
                    _STREAM_CONTROL_CACHE.pop(key, None)
                cached = _STREAM_CONTROL_CACHE.get(fingerprint)
                if cached is not None and cached[0] > now:
                    return cached[1]

        probe_id = str(uuid.uuid4())
        active = Deadline(timeout, self._clock)
        try:
            status, data, _ = self._request_json(
                "POST",
                "/v1/streams/lookup",
                deadline=active,
                expected=(200, 404, 405),
                body={"conversation_ids": [probe_id]},
            )
            if status == 200:
                result = (
                    StreamControlProbe(StreamControlSupport.SUPPORTED)
                    if isinstance(data, list)
                    else StreamControlProbe(
                        StreamControlSupport.UNKNOWN,
                        "invalid_response",
                    )
                )
            else:
                result = StreamControlProbe(
                    StreamControlSupport.UNSUPPORTED,
                    f"http_{status}",
                )
        except LlamaClientError as exc:
            if exc.status_code in {401, 403}:
                raise
            if isinstance(exc, DeadlineExceeded):
                reason = "timeout"
            elif exc.status_code is not None:
                reason = f"http_{exc.status_code}"
            else:
                reason = "transport"
            result = StreamControlProbe(StreamControlSupport.UNKNOWN, reason)

        with _STREAM_CONTROL_CACHE_LOCK:
            if (
                fingerprint not in _STREAM_CONTROL_CACHE
                and len(_STREAM_CONTROL_CACHE) >= STREAM_CONTROL_CACHE_MAX_ENTRIES
            ):
                oldest = min(_STREAM_CONTROL_CACHE, key=lambda key: _STREAM_CONTROL_CACHE[key][0])
                _STREAM_CONTROL_CACHE.pop(oldest, None)
            _STREAM_CONTROL_CACHE[fingerprint] = (
                now + STREAM_CONTROL_CACHE_SECONDS,
                result,
            )
        return result

    def prepare_stream_control(
        self,
        *,
        probe_timeout: float = STREAM_CONTROL_DELETE_TIMEOUT,
        delete_timeout: float = STREAM_CONTROL_DELETE_TIMEOUT,
        force_probe: bool = False,
    ) -> tuple[StreamControlProbe, StreamControl | None]:
        """Return an internally generated control only after a positive probe."""

        probe = self.probe_stream_control(timeout=probe_timeout, force=force_probe)
        if not probe.supported:
            return probe, None
        return probe, StreamControl(
            connection=self.connection,
            conversation_id=uuid.uuid4(),
            delete_timeout=delete_timeout,
        )

    def delete_stream(
        self,
        conversation_id: uuid.UUID,
        *,
        timeout: float = STREAM_CONTROL_DELETE_TIMEOUT,
    ) -> bool:
        """Delete one exact replay session; upstream treats unknown IDs idempotently."""

        if not isinstance(conversation_id, uuid.UUID):
            raise TypeError("conversation_id must be a UUID")
        if conversation_id.version != 4:
            raise ValueError("conversation_id must be an internally generated UUID4")
        if timeout <= 0 or not math.isfinite(timeout):
            raise ValueError("stream control delete timeout must be positive")
        status, _, _ = self._request_json(
            "DELETE",
            f"/v1/stream/{conversation_id}",
            deadline=Deadline(timeout, self._clock),
            expected=(200, 204),
        )
        return status in {200, 204}

    def request_load(
        self,
        model: str,
        *,
        deadline: Deadline | None = None,
        timeout: float | None = None,
    ) -> Mapping[str, Any]:
        active = self._deadline(deadline, timeout)
        _, data, _ = self._request_json(
            "POST",
            "/models/load",
            deadline=active,
            body={"model": model},
        )
        if not isinstance(data, Mapping) or data.get("success") is not True:
            raise ModelOperationError(f"router did not accept load request for '{model}'")
        return dict(data)

    def request_unload(
        self,
        model: str,
        *,
        deadline: Deadline | None = None,
        timeout: float | None = None,
    ) -> Mapping[str, Any]:
        active = self._deadline(deadline, timeout)
        _, data, _ = self._request_json(
            "POST",
            "/models/unload",
            deadline=active,
            body={"model": model},
        )
        if not isinstance(data, Mapping) or data.get("success") is not True:
            raise ModelOperationError(f"router did not accept unload request for '{model}'")
        return dict(data)

    def _check_cancel(self, cancel: CancelCheck | None, operation: str) -> None:
        if cancel is not None and cancel():
            raise OperationCancelled(f"{operation} was cancelled")

    def wait_for_model(
        self,
        model: str,
        terminal_states: Iterable[ModelState],
        *,
        deadline: Deadline,
        failure_states: Iterable[ModelState] = (),
        poll_interval: float = 0.5,
        cancel: CancelCheck | None = None,
        missing_is_unloaded: bool = False,
    ) -> RouterModel:
        if poll_interval < 0:
            raise ValueError("poll_interval must be non-negative")
        terminal = frozenset(terminal_states)
        failures = frozenset(failure_states)
        last: RouterModel | None = None

        while True:
            self._check_cancel(cancel, f"waiting for model '{model}'")
            deadline.raise_if_expired(f"waiting for model '{model}'")
            listed = self.models(deadline=deadline)
            try:
                current = self.find_model(model, listed)
            except ModelNotFoundError:
                if missing_is_unloaded:
                    return RouterModel(id=model, state=ModelState.UNLOADED)
                raise
            last = current
            if current.state in terminal:
                return current
            if current.state in failures:
                raise ModelOperationError(
                    f"model '{current.id}' entered terminal failure state {current.state.value}",
                    model=current,
                )

            remaining = deadline.remaining
            if remaining is not None and remaining <= 0:
                break
            delay = poll_interval if remaining is None else min(poll_interval, remaining)
            if delay > 0:
                self._sleeper(delay)

        state = last.state.value if last else "unknown"
        raise DeadlineExceeded(
            f"model '{model}' did not reach a terminal state; last state: {state}"
        )

    def load_model(
        self,
        model: str,
        *,
        timeout: float | None = None,
        deadline: Deadline | None = None,
        poll_interval: float = 0.5,
        cancel: CancelCheck | None = None,
    ) -> ModelOperationResult:
        active = self._deadline(deadline, timeout)
        self._check_cancel(cancel, f"loading model '{model}'")
        current = self.find_model(model, self.models(deadline=active))
        canonical = current.id
        if current.callable:
            return ModelOperationResult(
                current, accepted=False, completed=True, elapsed=active.elapsed
            )

        accepted = False
        if current.state != ModelState.LOADING:
            try:
                self.request_load(canonical, deadline=active)
                accepted = True
            except LlamaClientError as exc:
                # Make concurrent/idempotent callers safe without masking real failures.
                if exc.status_code not in {None, 400} or active.expired:
                    raise
                raced = self.find_model(canonical, self.models(deadline=active))
                if raced.state not in {ModelState.LOADING, ModelState.LOADED, ModelState.SLEEPING}:
                    raise

        final = self.wait_for_model(
            canonical,
            {ModelState.LOADED, ModelState.SLEEPING},
            deadline=active,
            failure_states={ModelState.FAILED},
            poll_interval=poll_interval,
            cancel=cancel,
        )
        return ModelOperationResult(
            final, accepted=accepted, completed=True, elapsed=active.elapsed
        )

    def unload_model(
        self,
        model: str,
        *,
        timeout: float | None = None,
        deadline: Deadline | None = None,
        poll_interval: float = 0.5,
        cancel: CancelCheck | None = None,
    ) -> ModelOperationResult:
        active = self._deadline(deadline, timeout)
        self._check_cancel(cancel, f"unloading model '{model}'")
        current = self.find_model(model, self.models(deadline=active))
        canonical = current.id
        if current.unloaded:
            return ModelOperationResult(
                current, accepted=False, completed=True, elapsed=active.elapsed
            )

        accepted = False
        try:
            self.request_unload(canonical, deadline=active)
            accepted = True
        except LlamaClientError as exc:
            if exc.status_code not in {None, 400} or active.expired:
                raise
            raced = self.find_model(canonical, self.models(deadline=active))
            if not raced.unloaded:
                raise

        final = self.wait_for_model(
            canonical,
            {ModelState.UNLOADED, ModelState.FAILED},
            deadline=active,
            poll_interval=poll_interval,
            cancel=cancel,
            missing_is_unloaded=True,
        )
        return ModelOperationResult(
            final, accepted=accepted, completed=True, elapsed=active.elapsed
        )

    def iter_model_events(
        self,
        *,
        timeout: float | None = None,
        deadline: Deadline | None = None,
        read_timeout: float | None = None,
        cancel: CancelCheck | None = None,
    ) -> Iterable[ModelEvent]:
        from .streaming import iter_model_events

        return iter_model_events(
            self.connection,
            timeout=timeout,
            deadline=deadline,
            read_timeout=read_timeout,
            cancel=cancel,
            session=self._session,
        )

    def stream_chat(
        self,
        payload: Mapping[str, Any],
        *,
        endpoint: str = "/v1/chat/completions",
        timeout: float | None = None,
        deadline: Deadline | None = None,
        chunk_timeout: float | None = None,
        on_chunk: Callable[[str, str], None] | None = None,
        on_update: Callable[[StreamUpdate], None] | None = None,
        cancel: CancelCheck | None = None,
        stream_control: StreamControl | None = None,
    ) -> StreamResult:
        from .streaming import (
            DEFAULT_MAX_FINISH_REASON_BYTES,
            DEFAULT_MAX_ID_BYTES,
            DEFAULT_MAX_MODEL_BYTES,
            DEFAULT_MAX_RESPONSE_BYTES,
            DEFAULT_MAX_SSE_EVENT_BYTES,
            DEFAULT_MAX_SSE_LINE_BYTES,
            DEFAULT_MAX_THINKING_BYTES,
            stream_chat,
        )

        return stream_chat(
            self.connection,
            payload,
            endpoint=endpoint,
            timeout=timeout,
            deadline=deadline,
            chunk_timeout=chunk_timeout,
            on_chunk=on_chunk,
            on_update=on_update,
            cancel=cancel,
            stream_control=stream_control,
            session=self._session,
            strict_protocol=True,
            max_line_bytes=DEFAULT_MAX_SSE_LINE_BYTES,
            max_event_bytes=DEFAULT_MAX_SSE_EVENT_BYTES,
            max_response_bytes=DEFAULT_MAX_RESPONSE_BYTES,
            max_thinking_bytes=DEFAULT_MAX_THINKING_BYTES,
            max_model_bytes=DEFAULT_MAX_MODEL_BYTES,
            max_id_bytes=DEFAULT_MAX_ID_BYTES,
            max_finish_reason_bytes=DEFAULT_MAX_FINISH_REASON_BYTES,
        )


__all__ = [
    "AmbiguousModelError",
    "AuthConfig",
    "CancelCheck",
    "ConnectionConfig",
    "Deadline",
    "DeadlineExceeded",
    "HealthStatus",
    "LlamaClientError",
    "LlamaServerClient",
    "ModelNotFoundError",
    "ModelOperationError",
    "ModelOperationResult",
    "ModelState",
    "OperationCancelled",
    "ResponseBodyLimitError",
    "ResponseProtocolError",
    "RouterModel",
    "ServerProps",
    "StreamCleanupOutcome",
    "StreamCleanupSnapshot",
    "StreamControl",
    "StreamControlProbe",
    "StreamControlSupport",
    "TLSConfig",
    "TokenizeResult",
    "clear_stream_control_cache",
    "parse_router_model",
    "redact_secrets",
]
