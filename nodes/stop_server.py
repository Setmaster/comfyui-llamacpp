"""Explicit stop, release, and status nodes for the owned runtime."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from ..models.catalog import ModelCatalog
from ..runtime.capabilities import (
    BinaryProbeError,
    BinaryResolutionError,
    probe_server_binary,
    probe_server_devices,
)
from ..server_manager import get_server_manager
from .presentation import NODE_CATEGORIES, NODE_SEARCH_ALIASES, apply_input_presentation

_SETUP_PROBE_TIMEOUT = 3.0
_MAX_WARNING_COUNT = 6
_MAX_WARNING_LENGTH = 240
_PROJECTOR_MODES = frozenset({"auto", "explicit", "none"})
_PROJECTOR_OUTCOMES = frozenset({"selected", "text_only"})


def _bounded_message(value: object, limit: int = _MAX_WARNING_LENGTH) -> str:
    rendered = " ".join(str(value).split())
    if len(rendered) <= limit:
        return rendered
    return rendered[: max(0, limit - 3)].rstrip() + "..."


def _safe_projector_status(value: object) -> dict[str, str | None] | None:
    if not isinstance(value, Mapping):
        return None
    mode = value.get("mode")
    outcome = value.get("outcome")
    if mode not in _PROJECTOR_MODES or outcome not in _PROJECTOR_OUTCOMES:
        return None
    projector = value.get("projector")
    if projector is not None:
        if (
            not isinstance(projector, str)
            or not projector
            or len(projector) > 4096
            or "\x00" in projector
        ):
            return None
        normalized = projector.replace("\\", "/")
        path = PurePosixPath(normalized)
        windows_path = PureWindowsPath(projector)
        if (
            ":" in projector
            or path.is_absolute()
            or windows_path.is_absolute()
            or bool(windows_path.drive)
            or any(part in ("", ".", "..") for part in path.parts)
        ):
            return None
        projector = normalized
    return {"mode": mode, "outcome": outcome, "projector": projector}


def _projector_status_line(value: object) -> str | None:
    status = _safe_projector_status(value)
    if status is None:
        return None
    mode = status["mode"]
    outcome = status["outcome"]
    projector = status["projector"]
    if outcome == "selected" and projector:
        qualifier = "auto-selected" if mode == "auto" else "explicit"
        return f"Vision projector: {projector} ({qualifier})"
    if mode == "none":
        return "Vision projector: none (text-only selected)"
    if mode == "auto":
        return "Vision projector: none (auto resolved to text-only)"
    return None


def _catalog_diagnostics() -> tuple[dict[str, Any], list[str]]:
    data: dict[str, Any] = {
        "entries": 0,
        "models": 0,
        "projectors": 0,
        "roots_configured": 0,
        "roots_present": 0,
        "roots_populated": 0,
        "router_presets": 0,
    }
    warnings: list[str] = []
    try:
        catalog = ModelCatalog()
        roots = tuple(catalog.roots)
        entries = tuple(catalog.entries())
        populated_roots = {entry.root for entry in entries}
        models = sum(not entry.is_mmproj for entry in entries)
        projectors = len(entries) - models
        data.update(
            {
                "entries": len(entries),
                "models": models,
                "projectors": projectors,
                "roots_configured": len(roots),
                "roots_present": sum(root.is_dir() for root in roots),
                "roots_populated": len(populated_roots),
                "router_presets": catalog.count_router_presets(),
            }
        )
        missing_roots = data["roots_configured"] - data["roots_present"]
        if missing_roots:
            warnings.append(
                f"{missing_roots} configured model root(s) do not exist; check ComfyUI model paths."
            )
        if models == 0:
            warnings.append(
                "No model GGUFs were found; add one under a configured ComfyUI LLM/gguf root."
            )
        elif data["router_presets"] == 0:
            warnings.append(
                "No router-visible presets were found; router mode scans root GGUFs and one bundle level."
            )
    except Exception as exc:
        warnings.append(f"Model catalog inspection failed: {_bounded_message(exc)}")
    return data, warnings


def _idle_binary_diagnostics(binary_path: str) -> tuple[dict[str, Any], list[str]]:
    data: dict[str, Any] = {
        "resolved": False,
        "path": None,
        "version": None,
        "build": None,
        "commit": None,
        "supports_router": None,
        "devices": [],
        "device_probe": "not_run",
    }
    warnings: list[str] = []
    requested = binary_path.strip() or None
    try:
        capabilities = probe_server_binary(requested, timeout=_SETUP_PROBE_TIMEOUT)
    except (BinaryResolutionError, BinaryProbeError, OSError, ValueError) as exc:
        data["error"] = _bounded_message(exc)
        warnings.append(
            "llama-server is not ready: set Binary Path, LLAMA_SERVER_BINARY or "
            "LLAMA_CPP_SERVER, or add llama-server to PATH."
        )
        return data, warnings

    data.update(
        {
            "resolved": True,
            "path": capabilities.path,
            "version": capabilities.version_line,
            "build": capabilities.build_number,
            "commit": capabilities.commit,
            "supports_router": capabilities.supports_router,
        }
    )
    if not capabilities.supports("--list-devices"):
        data["device_probe"] = "unsupported"
        warnings.append(
            "This llama-server build does not advertise --list-devices; offload devices are unknown."
        )
        return data, warnings

    try:
        devices = probe_server_devices(
            capabilities.path,
            timeout=_SETUP_PROBE_TIMEOUT,
        )
    except (BinaryResolutionError, BinaryProbeError, OSError, ValueError) as exc:
        data["device_probe"] = "failed"
        data["device_error"] = _bounded_message(exc)
        warnings.append(
            "llama-server device inspection failed; verify the complete backend runtime and driver."
        )
    else:
        data["device_probe"] = "complete"
        data["devices"] = list(devices)
        if not devices:
            warnings.append(
                "llama-server reported no offload devices; CPU inference may still be available."
            )
    return data, warnings


def _active_binary_diagnostics(
    status_data: dict[str, Any], binary_path: str
) -> tuple[dict[str, Any], list[str]]:
    capabilities = status_data.get("capabilities") or {}
    data = {
        "resolved": bool(capabilities.get("binary")),
        "path": capabilities.get("binary"),
        "version": capabilities.get("version"),
        "build": None,
        "commit": None,
        "supports_router": capabilities.get("supports_router"),
        "devices": [],
        "device_probe": "skipped_active_runtime",
    }
    warnings = []
    if binary_path.strip():
        warnings.append(
            "Binary Path is only probed while idle; diagnostics show the active managed binary."
        )
    return data, warnings


def _setup_diagnostics(status_data: dict[str, Any], binary_path: str) -> dict[str, Any]:
    catalog, warnings = _catalog_diagnostics()
    is_running = bool(status_data.get("is_running"))
    if is_running:
        binary, binary_warnings = _active_binary_diagnostics(status_data, binary_path)
    else:
        binary, binary_warnings = _idle_binary_diagnostics(binary_path)
    warnings.extend(binary_warnings)
    warnings = [_bounded_message(warning) for warning in warnings[:_MAX_WARNING_COUNT]]

    needs_attention = not binary["resolved"] or catalog["models"] == 0
    if is_running:
        state = "active_with_warnings" if warnings else "active"
    elif needs_attention:
        state = "needs_attention"
    else:
        state = "warning" if warnings else "ready"
    return {
        "state": state,
        "binary": binary,
        "catalog": catalog,
        "projector_auto_detection": {
            "supported": True,
            "strategy": "gguf_metadata",
            "ambiguous_requires_selection": True,
        },
        "warnings": warnings,
    }


class StopLlamaCppServer:
    DESCRIPTION = "Stops only the local llama-server process tree started by this node pack."
    CATEGORY = NODE_CATEGORIES["StopLlamaCppServer"]
    SEARCH_ALIASES = NODE_SEARCH_ALIASES["StopLlamaCppServer"]
    RETURN_TYPES = ("BOOLEAN", "STRING")
    RETURN_NAMES = ("success", "message")
    OUTPUT_TOOLTIPS = (
        "Whether the owned process tree stopped completely.",
        "Shutdown result or failure detail.",
    )
    FUNCTION = "stop_server"
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        schema = {
            "required": {},
            "optional": {
                "trigger": (
                    "*",
                    {"tooltip": "Optional dependency input used to sequence server shutdown."},
                )
            },
        }
        return apply_input_presentation("StopLlamaCppServer", schema)

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def stop_server(self, trigger=None):
        del trigger
        manager = get_server_manager()
        success, error = manager.stop()
        return (True, "Server stopped successfully") if success else (False, error or "Stop failed")


class LlamaCppReleaseRuntime:
    """Release model VRAM while retaining the router control process when possible."""

    DESCRIPTION = (
        "Releases this node pack's owned llama.cpp VRAM, deferring safely while a "
        "generation is active."
    )
    CATEGORY = NODE_CATEGORIES["LlamaCppReleaseRuntime"]
    SEARCH_ALIASES = NODE_SEARCH_ALIASES["LlamaCppReleaseRuntime"]
    RETURN_TYPES = ("BOOLEAN", "STRING", "STRING")
    RETURN_NAMES = ("success", "message", "result_json")
    OUTPUT_TOOLTIPS = (
        "Whether the release request was accepted; deferred requests complete after generation.",
        "Release status summary, including queued or terminal state.",
        "Complete release result as formatted JSON.",
    )
    FUNCTION = "release_runtime"
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        schema = {
            "required": {},
            "optional": {
                "trigger": (
                    "*",
                    {"tooltip": "Optional dependency input used to sequence VRAM release."},
                )
            },
        }
        return apply_input_presentation("LlamaCppReleaseRuntime", schema)

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def release_runtime(self, trigger=None):
        del trigger
        result = get_server_manager().runtime_service.request_release(source="explicit_node")
        payload = result.as_dict()
        message = f"Runtime release: {result.status.value}"
        if result.error:
            message += f" ({result.error})"
        return result.success, message, json.dumps(payload, indent=2)


class LlamaCppServerStatus:
    DESCRIPTION = (
        "Reports managed llama.cpp ownership, lifecycle, process identity, capabilities, "
        "model residency, and recent logs."
    )
    CATEGORY = NODE_CATEGORIES["LlamaCppServerStatus"]
    SEARCH_ALIASES = NODE_SEARCH_ALIASES["LlamaCppServerStatus"]
    RETURN_TYPES = ("BOOLEAN", "STRING", "STRING")
    RETURN_NAMES = ("is_running", "status", "info")
    OUTPUT_TOOLTIPS = (
        "Whether the owned llama-server process is running.",
        "Current process lifecycle state.",
        "Human-readable ownership, capability, residency, and log details.",
    )
    FUNCTION = "get_status"

    @classmethod
    def INPUT_TYPES(cls):
        schema = {
            "required": {},
            "optional": {
                "binary_path": (
                    "STRING",
                    {
                        "default": "",
                        "placeholder": "empty = environment or PATH",
                        "tooltip": (
                            "Optional llama-server executable to inspect while idle. Empty uses "
                            "LLAMA_SERVER_BINARY, LLAMA_CPP_SERVER, then PATH."
                        ),
                    },
                )
            },
        }
        return apply_input_presentation("LlamaCppServerStatus", schema)

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def get_status(self, binary_path: str = ""):
        binary_path = str(binary_path or "")
        data = get_server_manager().get_status_info()
        process = data.get("process") or {}
        runtime = data.get("runtime") or {}
        setup = _setup_diagnostics(data, binary_path)
        lines = [
            f"Status: {data['status']}",
            f"Mode: {data.get('mode', 'single_model')}",
            f"Owned: {runtime.get('owned', False)}",
            f"Lifecycle: {runtime.get('lifecycle', 'idle')}",
        ]
        if data.get("server_url"):
            lines.append(f"URL: {data['server_url']}")
        if process.get("pid") is not None:
            lines.append(f"PID: {process['pid']}")
        if process.get("process_group_id") is not None:
            lines.append(f"Process group: {process['process_group_id']}")
        if process.get("windows_job_assigned"):
            lines.append("Windows Job Object: assigned")
        if runtime.get("active_generations"):
            lines.append(f"Active generations: {runtime['active_generations']}")
        if runtime.get("release_pending"):
            lines.append("Release pending: yes")
        if data.get("capabilities"):
            lines.append(f"llama-server: {data['capabilities'].get('version', 'unknown')}")
        projector_line = _projector_status_line(data.get("projector"))
        if projector_line:
            lines.append(projector_line)
        binary = setup["binary"]
        catalog = setup["catalog"]
        lines.append(f"Setup: {setup['state'].replace('_', ' ')}")
        if binary.get("resolved"):
            lines.append(f"Binary: {binary.get('path')}")
            lines.append(f"Binary version: {binary.get('version') or 'unknown'}")
        else:
            lines.append("Binary: not resolved")
        devices = binary.get("devices") or []
        if devices:
            lines.append("Offload devices: " + "; ".join(devices))
        elif binary.get("device_probe") == "skipped_active_runtime":
            lines.append("Offload devices: not probed while the managed runtime is active")
        else:
            lines.append("Offload devices: none reported")
        lines.append(
            "Catalog: "
            f"{catalog['entries']} GGUF files "
            f"({catalog['models']} models, {catalog['projectors']} projectors); "
            f"roots {catalog['roots_configured']} configured, "
            f"{catalog['roots_present']} present, {catalog['roots_populated']} populated; "
            f"{catalog['router_presets']} router presets"
        )
        lines.append(
            "Projector auto-detection: compatible local pairs are selected automatically; "
            "ambiguous matches require an explicit choice."
        )
        lines.extend(f"Setup warning: {warning}" for warning in setup["warnings"])
        if data.get("last_error"):
            lines.append(f"Last error: {data['last_error']}")
        tail = process.get("log_tail") or []
        if tail:
            lines.append("Recent server log:")
            lines.extend(tail[-10:])
        diagnostics = {
            "schema": 1,
            "runtime": {
                "is_running": bool(data["is_running"]),
                "status": str(data["status"]),
                "mode": data.get("mode", "single_model"),
                "owned": bool(runtime.get("owned", False)),
                "lifecycle": runtime.get("lifecycle", "idle"),
                "pid": process.get("pid"),
                "projector": _safe_projector_status(data.get("projector")),
            },
            "setup": setup,
        }
        lines.append(
            "Diagnostics JSON: "
            + json.dumps(diagnostics, ensure_ascii=False, separators=(",", ":"))
        )
        info = "\n".join(lines)
        return {
            "ui": {"text": (info,)},
            "result": (bool(data["is_running"]), str(data["status"]), info),
        }


NODE_CLASS_MAPPINGS = {
    "StopLlamaCppServer": StopLlamaCppServer,
    "LlamaCppServerStatus": LlamaCppServerStatus,
    "LlamaCppReleaseRuntime": LlamaCppReleaseRuntime,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "StopLlamaCppServer": "Stop llama.cpp Server",
    "LlamaCppServerStatus": "llama.cpp Server Status",
    "LlamaCppReleaseRuntime": "Release llama.cpp VRAM",
}
