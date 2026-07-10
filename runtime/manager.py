"""Compatibility-oriented orchestration for the owned llama-server runtime."""

from __future__ import annotations

import atexit
import contextlib
import os
import socket
import threading
import time
from collections.abc import Callable, Iterator
from enum import Enum
from pathlib import Path
from typing import Any

if "." in __package__:
    from ..models.identity import RouterIdentityError, resolve_router_model
else:  # standalone runtime tests
    from models.identity import RouterIdentityError, resolve_router_model
from .capabilities import ServerCapabilities, probe_server_binary
from .client import (
    AuthConfig,
    ConnectionConfig,
    LlamaClientError,
    LlamaServerClient,
    RouterModel,
    TLSConfig,
)
from .comfy_bridge import evict_comfy_models
from .config import LaunchConfig, RouterConfig, ServerConfig
from .process import OwnedProcessController, ProcessLifecycle
from .service import ReleaseResult, RuntimeService, get_runtime_service


class ServerStatus(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


class ServerMode(str, Enum):
    SINGLE_MODEL = "single_model"
    ROUTER = "router"


def _port_is_bound(host: str, port: int) -> bool:
    target = "127.0.0.1" if host in {"0.0.0.0", "::", "*"} else host
    try:
        addresses = socket.getaddrinfo(target, port, type=socket.SOCK_STREAM)
    except OSError:
        return False
    for family, socktype, protocol, _, address in addresses:
        probe = socket.socket(family, socktype, protocol)
        probe.settimeout(0.2)
        try:
            if probe.connect_ex(address) == 0:
                return True
        finally:
            probe.close()
    return False


def _path_matches(left: str, right: str) -> bool:
    try:
        return os.path.normcase(str(Path(left).resolve())) == os.path.normcase(
            str(Path(right).resolve())
        )
    except OSError:
        return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


class LlamaCppServerManager:
    """Own one local llama-server and expose the historical manager API."""

    def __init__(
        self,
        *,
        runtime_service: RuntimeService | None = None,
        probe_binary: Callable[..., ServerCapabilities] = probe_server_binary,
        client_factory: Callable[[ConnectionConfig], LlamaServerClient] = LlamaServerClient,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        register_atexit: bool = False,
    ) -> None:
        self._runtime = runtime_service or get_runtime_service()
        self._process: OwnedProcessController = self._runtime.process_controller
        self._probe_binary = probe_binary
        self._client_factory = client_factory
        self._sleeper = sleeper
        self._clock = clock
        self._lock = threading.RLock()

        self._config: LaunchConfig | None = None
        self._config_fingerprint: str | None = None
        self._capabilities: ServerCapabilities | None = None
        self._connection: ConnectionConfig | None = None
        self._client: LlamaServerClient | None = None
        self._mode = ServerMode.SINGLE_MODEL
        self._status = ServerStatus.STOPPED
        self._last_error: str | None = None
        self._runtime.add_release_listener(self._on_release)
        if register_atexit:
            atexit.register(self._cleanup)

    @property
    def status(self) -> ServerStatus:
        with self._lock:
            process_state = self._process.state
            if self._status == ServerStatus.RUNNING and not self._process.is_running:
                self._status = (
                    ServerStatus.ERROR
                    if process_state
                    in {ProcessLifecycle.RUNTIME_FAILED, ProcessLifecycle.INCOMPLETE_STOP}
                    else ServerStatus.STOPPED
                )
                snapshot = self._process.snapshot()
                if self._status == ServerStatus.ERROR:
                    self._last_error = snapshot.last_error or (
                        f"llama-server exited with code {snapshot.returncode}"
                    )
            return self._status

    @property
    def is_running(self) -> bool:
        return self.status == ServerStatus.RUNNING and self._process.is_running

    @property
    def current_config(self) -> LaunchConfig | None:
        return self._config if self.is_running else None

    @property
    def mode(self) -> ServerMode:
        return self._mode

    @property
    def is_router_mode(self) -> bool:
        return self._mode == ServerMode.ROUTER and self.is_running

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def server_url(self) -> str:
        if self._connection is not None:
            return self._connection.base_url
        if self._config is not None:
            return f"http://{self._config.host}:{self._config.port}"
        return "http://127.0.0.1:8080"

    @property
    def capabilities(self) -> ServerCapabilities | None:
        return self._capabilities

    @property
    def client(self) -> LlamaServerClient | None:
        return self._client if self.is_running else None

    @property
    def runtime_service(self) -> RuntimeService:
        return self._runtime

    def connection_for(
        self,
        server_url: str = "",
        *,
        api_key_env: str = "LLAMACPP_API_KEY",
        verify_tls: bool | str = True,
        request_timeout: float | None = 300.0,
    ) -> tuple[ConnectionConfig, bool]:
        """Return a connection and whether it targets this owned runtime."""

        requested = server_url.strip()
        managed = not requested
        if managed:
            if not self.is_running or self._connection is None:
                raise RuntimeError(
                    "No owned llama-server is running. Start a server/router or provide server_url."
                )
            base_url = self._connection.base_url
        else:
            base_url = requested
            managed = self.is_running and base_url.rstrip("/") == self.server_url.rstrip("/")

        api_key = os.environ.get(api_key_env.strip()) if api_key_env.strip() else None
        connection = ConnectionConfig(
            base_url=base_url,
            auth=AuthConfig(api_key),
            tls=TLSConfig(verify_tls),
            default_deadline=request_timeout,
        )
        return connection, managed

    def client_for(
        self, server_url: str = "", **connection_options: Any
    ) -> tuple[LlamaServerClient, bool]:
        connection, managed = self.connection_for(server_url, **connection_options)
        if managed and self._client is not None and connection == self._connection:
            return self._client, True
        return self._client_factory(connection), managed

    @contextlib.contextmanager
    def generation_lease(self, *, managed: bool) -> Iterator[None]:
        if managed:
            with self._runtime.generation_lease():
                yield
        else:
            with contextlib.nullcontext():
                yield

    def health_check(self) -> bool:
        client = self.client
        if client is None:
            return False
        try:
            return client.health(timeout=2.0).ok
        except LlamaClientError:
            return False

    def start(
        self,
        config: ServerConfig,
        timeout: float | None = 60,
        *,
        binary_path: str | None = None,
        api_key_env: str = "LLAMACPP_API_KEY",
        verify_tls: bool = True,
        unload_comfy_models_before_start: bool = False,
    ) -> tuple[bool, str | None]:
        return self._start(
            config,
            ServerMode.SINGLE_MODEL,
            timeout,
            binary_path=binary_path,
            api_key_env=api_key_env,
            verify_tls=verify_tls,
            unload_comfy_models_before_start=unload_comfy_models_before_start,
        )

    def start_router(
        self,
        config: RouterConfig,
        timeout: float | None = 60,
        *,
        binary_path: str | None = None,
        api_key_env: str = "LLAMACPP_API_KEY",
        verify_tls: bool = True,
        unload_comfy_models_before_start: bool = False,
    ) -> tuple[bool, str | None]:
        return self._start(
            config,
            ServerMode.ROUTER,
            timeout,
            binary_path=binary_path,
            api_key_env=api_key_env,
            verify_tls=verify_tls,
            unload_comfy_models_before_start=unload_comfy_models_before_start,
        )

    def _start(
        self,
        config: LaunchConfig,
        mode: ServerMode,
        timeout: float | None,
        *,
        binary_path: str | None,
        api_key_env: str,
        verify_tls: bool,
        unload_comfy_models_before_start: bool,
    ) -> tuple[bool, str | None]:
        with self._runtime.serialized_operation(), self._lock:
            prior_status = self.status
            had_owned_process = self._process.has_owned_process
            destructive_start = False
            self._last_error = None
            try:
                capabilities = self._probe_binary(binary_path)
                self._validate_launch(config, mode, capabilities)
                fingerprint = config.fingerprint(capabilities.identity)

                if (
                    self.is_running
                    and self._config_fingerprint == fingerprint
                    and self.health_check()
                ):
                    return True, None

                if self._process.has_owned_process:
                    destructive_start = True
                    stopped, stop_error = self._stop_locked()
                    if not stopped:
                        raise RuntimeError(stop_error or "existing owned server did not stop")

                if _port_is_bound(config.host, config.port):
                    raise RuntimeError(
                        f"Port {config.host}:{config.port} is already in use; refusing to adopt it"
                    )

                if unload_comfy_models_before_start:
                    eviction = evict_comfy_models()
                    if not eviction.success:
                        raise RuntimeError(eviction.error or "Comfy model eviction failed")

                self._status = ServerStatus.STARTING
                destructive_start = True
                api_key = os.environ.get(api_key_env.strip()) if api_key_env.strip() else None
                connection = ConnectionConfig(
                    base_url=f"http://{config.host}:{config.port}",
                    auth=AuthConfig(api_key),
                    tls=TLSConfig(verify_tls),
                    default_deadline=timeout,
                )
                client = self._client_factory(connection)
                command = config.command(capabilities.path)
                self._process.start(command, secret_values=(api_key,) if api_key else ())

                self._config = config
                self._config_fingerprint = fingerprint
                self._capabilities = capabilities
                self._connection = connection
                self._client = client
                self._mode = mode
                self._wait_ready(config, mode, timeout)

                if mode == ServerMode.ROUTER:
                    self._runtime.configure_router_owned(client)
                else:
                    self._runtime.configure_direct_owned()
                self._status = ServerStatus.RUNNING
                return True, None
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                snapshot = self._process.snapshot()
                if snapshot.log_tail:
                    error += "\n\nllama-server log tail:\n" + "\n".join(snapshot.log_tail[-40:])
                self._last_error = error
                if had_owned_process and not destructive_start:
                    # Capability/config preflight must be transactional. A bad
                    # replacement request is not authority to tear down the
                    # healthy runtime that was already serving workflows.
                    self._status = prior_status
                    return False, self._last_error
                self._status = ServerStatus.ERROR
                if self._process.has_owned_process:
                    stop_result = self._process.stop()
                    if not stop_result.complete:
                        self._last_error += f"\nCleanup incomplete: {stop_result.error}"
                self._close_client()
                self._config = None
                self._config_fingerprint = None
                self._connection = None
                try:
                    self._runtime.clear_runtime()
                except RuntimeError:
                    pass
                return False, self._last_error

    def _validate_launch(
        self,
        config: LaunchConfig,
        mode: ServerMode,
        capabilities: ServerCapabilities,
    ) -> None:
        if mode == ServerMode.ROUTER:
            if not isinstance(config, RouterConfig):
                raise TypeError("router mode requires RouterConfig")
            if not capabilities.supports_router:
                raise RuntimeError("llama-server does not support router mode")
            if config.models_dir and not Path(config.models_dir).is_dir():
                raise FileNotFoundError(f"Models directory not found: {config.models_dir}")
            if not config.models_autoload and not capabilities.supports("--no-models-autoload"):
                raise RuntimeError("llama-server does not support --no-models-autoload")
            if config.models_preset and not capabilities.supports("--models-preset"):
                raise RuntimeError("llama-server does not support --models-preset")
        else:
            if not isinstance(config, ServerConfig):
                raise TypeError("direct mode requires ServerConfig")
            if not Path(config.model_path).is_file():
                raise FileNotFoundError(f"Model file not found: {config.model_path}")

        optional_flags = (
            (config.sleep_idle_seconds is not None, "--sleep-idle-seconds"),
            (config.api_key_file is not None, "--api-key-file"),
            (config.media_path is not None, "--media-path"),
            (config.mmproj_path is not None, "--mmproj"),
            (config.fit is not None, "--fit"),
            (config.flash_attention_mode is not None, "--flash-attn"),
        )
        unsupported = [
            flag for enabled, flag in optional_flags if enabled and not capabilities.supports(flag)
        ]
        if unsupported:
            raise RuntimeError(
                "llama-server does not support requested options: " + ", ".join(unsupported)
            )

    def _wait_ready(
        self,
        config: LaunchConfig,
        mode: ServerMode,
        timeout: float | None,
    ) -> None:
        if self._client is None:
            raise RuntimeError("llama-server client was not initialized")
        started = self._clock()
        last_error = "server did not answer"
        while timeout is None or self._clock() - started < timeout:
            if not self._process.is_running:
                snapshot = self._process.snapshot()
                raise RuntimeError(
                    f"llama-server exited during startup with code {snapshot.returncode}"
                )
            try:
                health = self._client.health(timeout=min(2.0, timeout or 2.0))
                if health.ok:
                    props = self._client.props(timeout=min(2.0, timeout or 2.0))
                    if mode == ServerMode.ROUTER and not props.is_router:
                        raise RuntimeError("healthy endpoint is not a llama-server router")
                    if mode == ServerMode.SINGLE_MODEL and props.is_router:
                        raise RuntimeError(
                            "healthy endpoint is a router, not the requested direct server"
                        )
                    if (
                        mode == ServerMode.SINGLE_MODEL
                        and props.model_path
                        and not _path_matches(props.model_path, config.model_path)
                    ):
                        raise RuntimeError(
                            "healthy endpoint model does not match the requested model path"
                        )
                    return
                last_error = health.message or health.status or f"health HTTP {health.status_code}"
            except LlamaClientError as exc:
                last_error = str(exc)
                if exc.status_code in {401, 403}:
                    raise RuntimeError(last_error) from exc
            self._sleeper(0.25)
        raise TimeoutError(f"llama-server readiness timed out: {last_error}")

    def stop(self) -> tuple[bool, str | None]:
        with self._runtime.serialized_operation(), self._lock:
            return self._stop_locked()

    def _stop_locked(self) -> tuple[bool, str | None]:
        if not self._process.has_owned_process:
            self._status = ServerStatus.STOPPED
            self._config = None
            self._config_fingerprint = None
            self._connection = None
            self._close_client()
            try:
                self._runtime.clear_runtime()
            except RuntimeError:
                pass
            return True, None

        self._status = ServerStatus.STOPPING
        result = self._process.stop()
        if result.complete:
            self._status = ServerStatus.STOPPED
            self._mode = ServerMode.SINGLE_MODEL
            self._config = None
            self._config_fingerprint = None
            self._capabilities = None
            self._connection = None
            self._close_client()
            self._runtime.clear_runtime()
            return True, None
        self._status = ServerStatus.ERROR
        self._last_error = result.error or f"Owned processes remain: {result.remaining_pids}"
        return False, self._last_error

    def list_models(self) -> tuple[bool, list[dict[str, Any]] | None, str | None]:
        if not self.is_running or self._client is None:
            return False, None, "Server not running"
        try:
            models = self._client.models()
            return True, [self._model_dict(model) for model in models], None
        except LlamaClientError as exc:
            return False, None, str(exc)

    @staticmethod
    def _model_dict(model: RouterModel) -> dict[str, Any]:
        result = dict(model.raw)
        result.setdefault("id", model.id)
        result.setdefault("status", {"value": model.state.value})
        return result

    def resolve_model_id(self, model_name: str) -> str:
        if not self.is_router_mode or self._client is None:
            return model_name
        records = [self._model_dict(model) for model in self._client.models()]
        return resolve_router_model(model_name, records)

    def load_model(self, model_name: str, timeout: float | None = 300) -> tuple[bool, str | None]:
        if not self.is_router_mode or self._client is None:
            return False, "Server not in router mode"
        try:
            model_id = self.resolve_model_id(model_name)
            result = self._client.load_model(model_id, timeout=timeout)
            if not result.success:
                return False, f"Model did not reach loaded state: {model_id}"
            return True, None
        except (LlamaClientError, RouterIdentityError, ValueError) as exc:
            return False, str(exc)

    def unload_model(self, model_name: str, timeout: float | None = 300) -> tuple[bool, str | None]:
        if not self.is_router_mode or self._client is None:
            return False, "Server not in router mode"
        try:
            model_id = self.resolve_model_id(model_name)
            result = self._client.unload_model(model_id, timeout=timeout)
            if not result.success:
                return False, f"Model did not reach unloaded state: {model_id}"
            return True, None
        except (LlamaClientError, RouterIdentityError, ValueError) as exc:
            return False, str(exc)

    def get_status_info(self) -> dict[str, Any]:
        status = self.status
        process = self._process.snapshot().as_dict()
        info: dict[str, Any] = {
            "status": status.value,
            "is_running": self.is_running,
            "mode": self._mode.value,
            "server_url": self.server_url if self.is_running else None,
            "last_error": self._last_error,
            "process": process,
            "runtime": self._runtime.diagnostics(),
        }
        if self._capabilities is not None:
            info["capabilities"] = {
                "binary": self._capabilities.path,
                "version": self._capabilities.version_line,
                "identity": self._capabilities.identity,
                "supports_router": self._capabilities.supports_router,
                "supports_idle_sleep": self._capabilities.supports_idle_sleep,
            }
        if self._config is not None:
            info["config"] = self._config.effective_values()
            if isinstance(self._config, ServerConfig):
                info["config"]["model"] = Path(self._config.model_path).name
        if process.get("pid") is not None:
            info["pid"] = process["pid"]
        return info

    def _on_release(self, result: ReleaseResult) -> None:
        if result.stop_result is None or not result.stop_result.complete:
            return
        with self._lock:
            self._status = ServerStatus.STOPPED
            self._mode = ServerMode.SINGLE_MODEL
            self._config = None
            self._config_fingerprint = None
            self._capabilities = None
            self._connection = None
            self._close_client()

    def _close_client(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            client.close()

    def _cleanup(self) -> None:
        try:
            self.stop()
        except Exception:
            pass


_SERVER_MANAGER: LlamaCppServerManager | None = None
_SERVER_MANAGER_LOCK = threading.Lock()


def get_server_manager() -> LlamaCppServerManager:
    global _SERVER_MANAGER
    if _SERVER_MANAGER is None:
        with _SERVER_MANAGER_LOCK:
            if _SERVER_MANAGER is None:
                _SERVER_MANAGER = LlamaCppServerManager(register_atexit=True)
    return _SERVER_MANAGER


__all__ = [
    "LlamaCppServerManager",
    "RouterConfig",
    "ServerConfig",
    "ServerMode",
    "ServerStatus",
    "get_server_manager",
]
