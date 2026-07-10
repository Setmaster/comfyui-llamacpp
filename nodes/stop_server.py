"""Explicit stop, release, and status nodes for the owned runtime."""

from __future__ import annotations

import json

from ..server_manager import get_server_manager


class StopLlamaCppServer:
    DESCRIPTION = "Stops only the local llama-server process tree started by this node pack."
    CATEGORY = "LlamaCpp"
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
        return {
            "required": {},
            "optional": {
                "trigger": (
                    "*",
                    {"tooltip": "Optional dependency input used to sequence server shutdown."},
                )
            },
        }

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
    CATEGORY = "LlamaCpp"
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
        return {
            "required": {},
            "optional": {
                "trigger": (
                    "*",
                    {"tooltip": "Optional dependency input used to sequence VRAM release."},
                )
            },
        }

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
    CATEGORY = "LlamaCpp"
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
        return {"required": {}}

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def get_status(self):
        data = get_server_manager().get_status_info()
        process = data.get("process") or {}
        runtime = data.get("runtime") or {}
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
        if data.get("last_error"):
            lines.append(f"Last error: {data['last_error']}")
        tail = process.get("log_tail") or []
        if tail:
            lines.append("Recent server log:")
            lines.extend(tail[-10:])
        return bool(data["is_running"]), str(data["status"]), "\n".join(lines)


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
