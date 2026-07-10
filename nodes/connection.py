"""Reusable, secret-free llama-server connection profile node."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LlamaCppConnectionProfile:
    server_url: str = ""
    model: str = ""
    api_key_env: str = "LLAMACPP_API_KEY"
    verify_tls: bool = True
    request_timeout: int = 300


class LlamaCppConnection:
    CATEGORY = "LlamaCpp"
    RETURN_TYPES = ("LLAMACPP_CONNECTION",)
    RETURN_NAMES = ("connection",)
    FUNCTION = "create_connection"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "server_url": (
                    "STRING",
                    {
                        "default": "",
                        "placeholder": "empty = server owned by this pack",
                        "tooltip": "Local llama-server URL, or empty for the managed server.",
                    },
                )
            },
            "optional": {
                "model": (
                    "STRING",
                    {"default": "", "tooltip": "Optional default router model ID."},
                ),
                "api_key_env": (
                    "STRING",
                    {
                        "default": "LLAMACPP_API_KEY",
                        "tooltip": "Environment variable containing the API key.",
                    },
                ),
                "verify_tls": ("BOOLEAN", {"default": True}),
                "request_timeout": (
                    "INT",
                    {"default": 300, "min": 1, "max": 86400, "step": 1},
                ),
            },
        }

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def create_connection(
        self,
        server_url: str,
        model: str = "",
        api_key_env: str = "LLAMACPP_API_KEY",
        verify_tls: bool = True,
        request_timeout: int = 300,
    ):
        return (
            LlamaCppConnectionProfile(
                server_url=server_url.strip(),
                model=model.strip(),
                api_key_env=api_key_env.strip(),
                verify_tls=verify_tls,
                request_timeout=request_timeout,
            ),
        )


NODE_CLASS_MAPPINGS = {"LlamaCppConnection": LlamaCppConnection}
NODE_DISPLAY_NAME_MAPPINGS = {"LlamaCppConnection": "llama.cpp Connection"}
