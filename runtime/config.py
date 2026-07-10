"""Immutable llama-server launch configuration.

The node layer historically exposed a small set of llama.cpp flags.  These
objects deliberately preserve those defaults while providing a complete,
stable fingerprint for lifecycle idempotency checks.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from typing import Any

GpuLayers = int | str


_RESERVED_EXTRA_FLAGS = frozenset(
    {
        "-m",
        "--model",
        "-mu",
        "--model-url",
        "-dr",
        "--docker-repo",
        "-hf",
        "-hfr",
        "--hf-repo",
        "-hff",
        "--hf-file",
        "-hft",
        "--hf-token",
        "-a",
        "--alias",
        "--port",
        "--host",
        "--reuse-port",
        "--api-prefix",
        "-c",
        "--ctx-size",
        "-ngl",
        "--gpu-layers",
        "--n-gpu-layers",
        "-mg",
        "--main-gpu",
        "-ts",
        "--tensor-split",
        "-t",
        "--threads",
        "-b",
        "--batch-size",
        "-fa",
        "--flash-attn",
        "--no-mmap",
        "--mmap",
        "-mm",
        "--mmproj",
        "-mmu",
        "--mmproj-url",
        "--mmproj-auto",
        "--no-mmproj",
        "--sleep-idle-seconds",
        "--api-key",
        "--api-key-file",
        "--ssl-key-file",
        "--ssl-cert-file",
        "--media-path",
        "--fit",
        "-fit",
        "--models-dir",
        "--models-preset",
        "--models-max",
        "--models-autoload",
        "--no-models-autoload",
    }
)


def _path_text(value: str | os.PathLike[str]) -> str:
    return os.fspath(value)


def _validate_extra_args(extra_args: tuple[str, ...]) -> None:
    for argument in extra_args:
        option = argument.partition("=")[0].lower()
        if option in _RESERVED_EXTRA_FLAGS:
            raise ValueError(
                f"extra_args cannot override typed option {option!r}; "
                "use its dedicated configuration field"
            )


def _validate_common(
    *,
    host: str,
    port: int,
    context_size: int,
    n_gpu_layers: GpuLayers,
    main_gpu: int,
    threads: int | None,
    batch_size: int | None,
    flash_attention_mode: str | None,
    sleep_idle_seconds: int | None,
) -> None:
    if not host.strip():
        raise ValueError("host must not be empty")
    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    if context_size < 0:
        raise ValueError("context_size must be non-negative")
    if isinstance(n_gpu_layers, str):
        if n_gpu_layers not in {"auto", "all"}:
            try:
                int(n_gpu_layers)
            except ValueError as exc:
                raise ValueError("n_gpu_layers must be an integer, 'auto', or 'all'") from exc
    if main_gpu < 0:
        raise ValueError("main_gpu must be non-negative")
    if threads is not None and threads <= 0:
        raise ValueError("threads must be positive when set")
    if batch_size is not None and batch_size <= 0:
        raise ValueError("batch_size must be positive when set")
    if flash_attention_mode not in {None, "on", "off", "auto"}:
        raise ValueError("flash_attention_mode must be 'on', 'off', 'auto', or None")
    if sleep_idle_seconds is not None and sleep_idle_seconds < -1:
        raise ValueError("sleep_idle_seconds must be -1 or greater")


def _fingerprint_payload(config: Any, mode: str, binary_identity: str | None) -> str:
    payload = {
        "schema": 1,
        "mode": mode,
        "binary_identity": binary_identity,
        "config": asdict(config),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return sha256(encoded.encode("utf-8")).hexdigest()


def _append_common_args(
    args: list[str],
    *,
    tensor_split: str | None,
    threads: int | None,
    batch_size: int | None,
    flash_attention: bool,
    flash_attention_mode: str | None,
    no_mmap: bool,
    mmproj_path: str | None,
    sleep_idle_seconds: int | None,
    api_key_file: str | None,
    media_path: str | None,
    fit: bool | None,
    extra_args: tuple[str, ...],
) -> None:
    if tensor_split:
        args.extend(("--tensor-split", tensor_split))
    if threads is not None:
        args.extend(("-t", str(threads)))
    if batch_size is not None:
        args.extend(("-b", str(batch_size)))
    if flash_attention_mode is not None:
        args.extend(("-fa", flash_attention_mode))
    elif flash_attention:
        # The saved-workflow surface remains a legacy boolean, but current
        # llama-server treats -fa as a value-taking option. Preserve the
        # user's intended behavior by translating True to the explicit mode.
        args.extend(("-fa", "on"))
    if no_mmap:
        args.append("--no-mmap")
    if mmproj_path:
        args.extend(("--mmproj", mmproj_path))
    if sleep_idle_seconds is not None:
        args.extend(("--sleep-idle-seconds", str(sleep_idle_seconds)))
    if api_key_file:
        args.extend(("--api-key-file", api_key_file))
    if media_path:
        args.extend(("--media-path", media_path))
    if fit is not None:
        args.extend(("--fit", "on" if fit else "off"))
    args.extend(extra_args)


@dataclass(frozen=True, slots=True)
class ServerConfig:
    """Single-model server configuration.

    The first eleven fields and their defaults match the original public
    ``ServerConfig`` so existing callers can migrate without changing their
    effective launch command.
    """

    model_path: str
    port: int = 8080
    host: str = "127.0.0.1"
    context_size: int = 4096
    n_gpu_layers: GpuLayers = 999
    main_gpu: int = 0
    tensor_split: str | None = None
    threads: int | None = None
    batch_size: int | None = None
    flash_attention: bool = False
    no_mmap: bool = False
    mmproj_path: str | None = None
    sleep_idle_seconds: int | None = None
    api_key_file: str | None = None
    media_path: str | None = None
    flash_attention_mode: str | None = None
    fit: bool | None = None
    extra_args: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "model_path", _path_text(self.model_path))
        object.__setattr__(self, "extra_args", tuple(self.extra_args))
        for name in ("mmproj_path", "api_key_file", "media_path"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _path_text(value))
        if not self.model_path:
            raise ValueError("model_path must not be empty")
        if not all(isinstance(argument, str) for argument in self.extra_args):
            raise TypeError("extra_args must contain only strings")
        _validate_extra_args(self.extra_args)
        if self.flash_attention and self.flash_attention_mode is not None:
            raise ValueError("set either flash_attention or flash_attention_mode, not both")
        _validate_common(
            host=self.host,
            port=self.port,
            context_size=self.context_size,
            n_gpu_layers=self.n_gpu_layers,
            main_gpu=self.main_gpu,
            threads=self.threads,
            batch_size=self.batch_size,
            flash_attention_mode=self.flash_attention_mode,
            sleep_idle_seconds=self.sleep_idle_seconds,
        )

    @property
    def mode(self) -> str:
        return "single_model"

    def to_command_args(self) -> list[str]:
        args = [
            "-m",
            self.model_path,
            "--port",
            str(self.port),
            "--host",
            self.host,
            "-c",
            str(self.context_size),
            "-ngl",
            str(self.n_gpu_layers),
            "--main-gpu",
            str(self.main_gpu),
        ]
        _append_common_args(
            args,
            tensor_split=self.tensor_split,
            threads=self.threads,
            batch_size=self.batch_size,
            flash_attention=self.flash_attention,
            flash_attention_mode=self.flash_attention_mode,
            no_mmap=self.no_mmap,
            mmproj_path=self.mmproj_path,
            sleep_idle_seconds=self.sleep_idle_seconds,
            api_key_file=self.api_key_file,
            media_path=self.media_path,
            fit=self.fit,
            extra_args=self.extra_args,
        )
        return args

    def command(self, binary: str | os.PathLike[str]) -> list[str]:
        return [_path_text(binary), *self.to_command_args()]

    def fingerprint(self, binary_identity: str | None = None) -> str:
        return _fingerprint_payload(self, self.mode, binary_identity)

    def config_hash(self, binary_identity: str | None = None) -> str:
        """Compatibility alias for the original manager API."""

        return self.fingerprint(binary_identity)

    def effective_values(self) -> dict[str, Any]:
        return {"mode": self.mode, **asdict(self)}


@dataclass(frozen=True, slots=True)
class RouterConfig:
    """Multi-model router configuration preserving the original defaults."""

    models_dir: str
    port: int = 8080
    host: str = "127.0.0.1"
    context_size: int = 4096
    n_gpu_layers: GpuLayers = 999
    main_gpu: int = 0
    threads: int | None = None
    batch_size: int | None = None
    flash_attention: bool = False
    models_max: int = 4
    models_autoload: bool = True
    tensor_split: str | None = None
    no_mmap: bool = False
    models_preset: str | None = None
    mmproj_path: str | None = None
    sleep_idle_seconds: int | None = None
    api_key_file: str | None = None
    media_path: str | None = None
    flash_attention_mode: str | None = None
    fit: bool | None = None
    extra_args: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "models_dir", _path_text(self.models_dir))
        object.__setattr__(self, "extra_args", tuple(self.extra_args))
        for name in ("models_preset", "mmproj_path", "api_key_file", "media_path"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _path_text(value))
        if not self.models_dir and not self.models_preset:
            raise ValueError("models_dir or models_preset must be set")
        if not all(isinstance(argument, str) for argument in self.extra_args):
            raise TypeError("extra_args must contain only strings")
        _validate_extra_args(self.extra_args)
        if self.flash_attention and self.flash_attention_mode is not None:
            raise ValueError("set either flash_attention or flash_attention_mode, not both")
        if self.models_max < 0:
            raise ValueError("models_max must be non-negative")
        _validate_common(
            host=self.host,
            port=self.port,
            context_size=self.context_size,
            n_gpu_layers=self.n_gpu_layers,
            main_gpu=self.main_gpu,
            threads=self.threads,
            batch_size=self.batch_size,
            flash_attention_mode=self.flash_attention_mode,
            sleep_idle_seconds=self.sleep_idle_seconds,
        )

    @property
    def mode(self) -> str:
        return "router"

    def to_command_args(self) -> list[str]:
        args: list[str] = []
        if self.models_dir:
            args.extend(("--models-dir", self.models_dir))
        if self.models_preset:
            args.extend(("--models-preset", self.models_preset))
        args.extend(
            (
                "--port",
                str(self.port),
                "--host",
                self.host,
                "-c",
                str(self.context_size),
                "-ngl",
                str(self.n_gpu_layers),
                "--main-gpu",
                str(self.main_gpu),
                "--models-max",
                str(self.models_max),
            )
        )
        if not self.models_autoload:
            args.append("--no-models-autoload")
        _append_common_args(
            args,
            tensor_split=self.tensor_split,
            threads=self.threads,
            batch_size=self.batch_size,
            flash_attention=self.flash_attention,
            flash_attention_mode=self.flash_attention_mode,
            no_mmap=self.no_mmap,
            mmproj_path=self.mmproj_path,
            sleep_idle_seconds=self.sleep_idle_seconds,
            api_key_file=self.api_key_file,
            media_path=self.media_path,
            fit=self.fit,
            extra_args=self.extra_args,
        )
        return args

    def command(self, binary: str | os.PathLike[str]) -> list[str]:
        return [_path_text(binary), *self.to_command_args()]

    def fingerprint(self, binary_identity: str | None = None) -> str:
        return _fingerprint_payload(self, self.mode, binary_identity)

    def config_hash(self, binary_identity: str | None = None) -> str:
        return self.fingerprint(binary_identity)

    def effective_values(self) -> dict[str, Any]:
        return {"mode": self.mode, **asdict(self)}


LaunchConfig = ServerConfig | RouterConfig


__all__ = ["GpuLayers", "LaunchConfig", "RouterConfig", "ServerConfig"]
