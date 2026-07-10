"""Parsing helpers shared by direct and router start nodes."""

from __future__ import annotations

import os
import shlex


def parse_gpu_layers(gpu_layers: str) -> int | str:
    """Preserve empty as legacy all-layers 999, while accepting modern modes."""

    value = gpu_layers.strip().lower()
    if not value:
        return 999
    if value in {"auto", "all"}:
        return value
    try:
        return int(value)
    except ValueError:
        print(f"[llama.cpp] Invalid gpu_layers value {gpu_layers!r}; using legacy all layers")
        return 999


def parse_threads(threads: str) -> int | None:
    value = threads.strip()
    if not value:
        return None
    try:
        parsed = int(value)
        return parsed if parsed > 0 else None
    except ValueError:
        print(f"[llama.cpp] Invalid threads value {threads!r}; using auto")
        return None


def parse_timeout(timeout: str) -> int | None:
    value = timeout.strip()
    if not value:
        return None
    try:
        parsed = int(value)
        return parsed if parsed > 0 else None
    except ValueError:
        print(f"[llama.cpp] Invalid timeout value {timeout!r}; using 60 seconds")
        return 60


def optional_path(path: str) -> str | None:
    value = os.path.expandvars(os.path.expanduser(path.strip()))
    return value or None


def parse_extra_args(value: str) -> tuple[str, ...]:
    if not value.strip():
        return ()
    try:
        return tuple(shlex.split(value, posix=os.name != "nt"))
    except ValueError as exc:
        raise ValueError(f"Invalid extra llama-server arguments: {exc}") from exc
