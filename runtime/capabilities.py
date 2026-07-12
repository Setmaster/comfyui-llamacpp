"""llama-server binary discovery and capability probing."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path


class BinaryResolutionError(FileNotFoundError):
    """Raised when a usable llama-server executable cannot be resolved."""


class BinaryProbeError(RuntimeError):
    """Raised when a resolved executable cannot report its capabilities."""


@dataclass(frozen=True, slots=True)
class ServerCapabilities:
    path: str
    version_output: str
    help_output: str
    flags: frozenset[str]
    identity: str
    build_number: int | None = None
    commit: str | None = None

    def supports(self, flag: str) -> bool:
        normalized = flag if flag.startswith("--") else f"--{flag.lstrip('-')}"
        return normalized in self.flags

    @property
    def supports_router(self) -> bool:
        return self.supports("--models-dir") and self.supports("--models-max")

    @property
    def supports_idle_sleep(self) -> bool:
        return self.supports("--sleep-idle-seconds")

    @property
    def supports_api_key_file(self) -> bool:
        return self.supports("--api-key-file")

    @property
    def supports_symbolic_gpu_layers(self) -> bool:
        option_help = _option_help(
            self.help_output,
            ("-ngl", "--gpu-layers", "--n-gpu-layers"),
        ).lower()
        return bool(option_help) and "auto" in option_help and "all" in option_help

    @property
    def version_line(self) -> str:
        return next(
            (line.strip() for line in self.version_output.splitlines() if line.strip()), "unknown"
        )


ProbeRunner = Callable[..., subprocess.CompletedProcess[str]]

_CACHE_LOCK = threading.Lock()
_CAPABILITY_CACHE: dict[tuple[str, int, int], ServerCapabilities] = {}
_LONG_FLAG_RE = re.compile(r"(?<![\w-])(--[a-z0-9][a-z0-9-]*)")
_OPTION_START_RE = re.compile(r"^\s*-{1,2}[a-z0-9]", re.IGNORECASE)


def _option_help(help_output: str, aliases: Sequence[str]) -> str:
    """Return one option's help block without bleeding into later options."""

    lines = help_output.splitlines()
    for index, line in enumerate(lines):
        if not any(re.search(rf"(?<![\w-]){re.escape(alias)}(?![\w-])", line) for alias in aliases):
            continue
        block = [line]
        for continuation in lines[index + 1 :]:
            if _OPTION_START_RE.match(continuation):
                break
            if continuation.strip():
                block.append(continuation)
        return "\n".join(block)
    return ""


def _candidate_from_text(candidate: str) -> Path | None:
    expanded = os.path.expandvars(os.path.expanduser(candidate))
    path = Path(expanded)
    has_separator = os.sep in expanded or (os.altsep is not None and os.altsep in expanded)
    if path.is_absolute() or has_separator:
        return path
    resolved = shutil.which(expanded)
    return Path(resolved) if resolved else None


def _validate_binary(path: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise BinaryResolutionError(f"llama-server executable does not exist: {path}") from exc
    if not resolved.is_file():
        raise BinaryResolutionError(f"llama-server path is not a file: {resolved}")
    if os.name != "nt" and not os.access(resolved, os.X_OK):
        raise BinaryResolutionError(f"llama-server is not executable: {resolved}")
    return resolved


def resolve_server_binary(
    explicit: os.PathLike[str] | str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    names: Sequence[str] | None = None,
) -> Path:
    """Resolve a llama-server executable without invoking it.

    Resolution order is an explicit argument, ``LLAMA_SERVER_BINARY``,
    ``LLAMA_CPP_SERVER``, then the platform executable name on ``PATH``.
    """

    environment = os.environ if env is None else env
    candidates: list[str] = []
    if explicit is not None:
        candidates.append(os.fspath(explicit))
    else:
        for key in ("LLAMA_SERVER_BINARY", "LLAMA_CPP_SERVER"):
            value = environment.get(key)
            if value:
                candidates.append(value)
        candidates.extend(
            names or (("llama-server.exe",) if os.name == "nt" else ("llama-server",))
        )

    attempted: list[str] = []
    for candidate in candidates:
        attempted.append(candidate)
        path = _candidate_from_text(candidate)
        if path is None:
            continue
        try:
            return _validate_binary(path)
        except BinaryResolutionError:
            if explicit is not None:
                raise

    rendered = ", ".join(attempted) if attempted else "llama-server"
    raise BinaryResolutionError(f"llama-server not found; checked: {rendered}")


def _run_probe(runner: ProbeRunner, path: Path, argument: str, timeout: float) -> str:
    try:
        completed = runner(
            [str(path), argument],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise BinaryProbeError(f"llama-server {argument} timed out after {timeout:g}s") from exc
    except OSError as exc:
        raise BinaryProbeError(f"failed to execute llama-server {argument}: {exc}") from exc

    output = completed.stdout or ""
    if completed.returncode != 0:
        excerpt = output.strip().splitlines()[-1:] or ["no output"]
        raise BinaryProbeError(
            f"llama-server {argument} exited with {completed.returncode}: {excerpt[0][:300]}"
        )
    return output


def _parse_build_number(version_output: str) -> int | None:
    for pattern in (
        r"\bversion\s*:\s*b?(\d+)\b",
        r"\bbuild(?:\s+number)?\s*[:=]?\s*b?(\d+)\b",
        r"\bb(\d{3,})\b",
    ):
        match = re.search(pattern, version_output, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _parse_commit(version_output: str) -> str | None:
    match = re.search(r"\b(?:commit\s*[:=]?\s*)?([0-9a-f]{7,40})\b", version_output, re.IGNORECASE)
    return match.group(1).lower() if match else None


def _identity(path: Path, stat: os.stat_result, version_output: str, help_output: str) -> str:
    payload = {
        "path": str(path),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "version_sha256": sha256(version_output.encode("utf-8", errors="replace")).hexdigest(),
        "help_sha256": sha256(help_output.encode("utf-8", errors="replace")).hexdigest(),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def probe_server_binary(
    binary: os.PathLike[str] | str | None = None,
    *,
    timeout: float = 10.0,
    refresh: bool = False,
    env: Mapping[str, str] | None = None,
    runner: ProbeRunner | None = None,
) -> ServerCapabilities:
    """Run bounded ``--version`` and ``--help`` probes.

    Real probes are cached by canonical path, size, and modification time.
    An injected runner is intentionally not cached, keeping tests deterministic.
    """

    if timeout <= 0:
        raise ValueError("probe timeout must be positive")
    path = resolve_server_binary(binary, env=env)
    stat = path.stat()
    cache_key = (str(path), stat.st_size, stat.st_mtime_ns)

    if runner is None and not refresh:
        with _CACHE_LOCK:
            cached = _CAPABILITY_CACHE.get(cache_key)
        if cached is not None:
            return cached

    run = subprocess.run if runner is None else runner
    version_output = _run_probe(run, path, "--version", timeout)
    help_output = _run_probe(run, path, "--help", timeout)
    flags = frozenset(_LONG_FLAG_RE.findall(help_output.lower()))
    result = ServerCapabilities(
        path=str(path),
        version_output=version_output,
        help_output=help_output,
        flags=flags,
        identity=_identity(path, stat, version_output, help_output),
        build_number=_parse_build_number(version_output),
        commit=_parse_commit(version_output),
    )

    if runner is None:
        with _CACHE_LOCK:
            stale = [key for key in _CAPABILITY_CACHE if key[0] == str(path) and key != cache_key]
            for key in stale:
                _CAPABILITY_CACHE.pop(key, None)
            _CAPABILITY_CACHE[cache_key] = result
    return result


def probe_server_devices(
    binary: os.PathLike[str] | str | None = None,
    *,
    timeout: float = 5.0,
    env: Mapping[str, str] | None = None,
    runner: ProbeRunner | None = None,
    max_lines: int = 16,
    max_line_length: int = 300,
) -> tuple[str, ...]:
    """Return a bounded snapshot from llama-server ``--list-devices``.

    Device availability can change while ComfyUI is running, so this optional
    probe is intentionally separate from the cached version/help capability
    probe used for every managed launch.
    """

    if timeout <= 0:
        raise ValueError("probe timeout must be positive")
    if max_lines <= 0:
        raise ValueError("max_lines must be positive")
    if max_line_length <= 0:
        raise ValueError("max_line_length must be positive")

    path = resolve_server_binary(binary, env=env)
    run = subprocess.run if runner is None else runner
    output = _run_probe(run, path, "--list-devices", timeout)
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if lines and lines[0].casefold().rstrip(":") == "available devices":
        lines.pop(0)
    return tuple(line[:max_line_length] for line in lines[:max_lines])


def clear_capability_cache() -> None:
    with _CACHE_LOCK:
        _CAPABILITY_CACHE.clear()


__all__ = [
    "BinaryProbeError",
    "BinaryResolutionError",
    "ServerCapabilities",
    "clear_capability_cache",
    "probe_server_binary",
    "probe_server_devices",
    "resolve_server_binary",
]
