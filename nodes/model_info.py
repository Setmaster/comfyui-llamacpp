"""Server properties and active model information node."""

from __future__ import annotations

import json

from ..runtime.client import LlamaServerClient
from ..server_manager import get_server_manager
from .connection import LlamaCppConnectionProfile
from .presentation import NODE_CATEGORIES, NODE_SEARCH_ALIASES, apply_input_presentation


class LlamaCppModelInfo:
    DESCRIPTION = (
        "Reads the active model identity, context length, and server metadata from llama-server."
    )
    CATEGORY = NODE_CATEGORIES["LlamaCppModelInfo"]
    SEARCH_ALIASES = NODE_SEARCH_ALIASES["LlamaCppModelInfo"]
    RETURN_TYPES = ("STRING", "INT", "STRING")
    RETURN_NAMES = ("model_name", "context_length", "info_json")
    OUTPUT_TOOLTIPS = (
        "Active model alias, path, or router identity.",
        "Context length reported by llama-server.",
        "Complete server properties as formatted JSON.",
    )
    FUNCTION = "get_info"
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        schema = {
            "required": {},
            "optional": {
                "server_url": (
                    "STRING",
                    {
                        "default": "",
                        "tooltip": "llama-server URL, or empty for the managed runtime.",
                    },
                ),
                "trigger": (
                    "*",
                    {"tooltip": "Optional dependency input used to refresh model metadata."},
                ),
                "model": (
                    "STRING",
                    {"default": "", "tooltip": "Optional exact router model ID."},
                ),
                "api_key_env": (
                    "STRING",
                    {
                        "default": "LLAMACPP_API_KEY",
                        "tooltip": "Environment variable containing the API key.",
                    },
                ),
                "verify_tls": (
                    "BOOLEAN",
                    {"default": True, "tooltip": "Verify HTTPS certificates."},
                ),
                "request_timeout": (
                    "INT",
                    {
                        "default": 30,
                        "min": 1,
                        "max": 3600,
                        "tooltip": "Server metadata deadline in seconds.",
                    },
                ),
                "connection": (
                    "LLAMACPP_CONNECTION",
                    {"tooltip": "Optional reusable connection profile."},
                ),
            },
        }
        return apply_input_presentation("LlamaCppModelInfo", schema)

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def get_info(
        self,
        server_url: str = "",
        trigger=None,
        model: str = "",
        api_key_env: str = "LLAMACPP_API_KEY",
        verify_tls: bool = True,
        request_timeout: int = 30,
        connection: LlamaCppConnectionProfile | None = None,
    ):
        del trigger
        manager = get_server_manager()
        if connection:
            server_url = connection.server_url or server_url
            model = connection.model or model
            api_key_env = connection.api_key_env
            verify_tls = connection.verify_tls
            request_timeout = connection.request_timeout
        try:
            config, managed = manager.connection_for(
                server_url,
                api_key_env=api_key_env,
                verify_tls=verify_tls,
                request_timeout=request_timeout,
            )
            with manager.generation_lease(managed=managed), LlamaServerClient(config) as client:
                model_id = manager.resolve_model_id(model) if managed and model else model or None
                props = client.props(model_id, timeout=request_timeout)
            raw = dict(props.raw)
            generation = raw.get("default_generation_settings") or {}
            params = generation.get("params") if isinstance(generation, dict) else {}
            context = generation.get("n_ctx", 0) if isinstance(generation, dict) else 0
            if not context and isinstance(params, dict):
                context = params.get("n_ctx", 0)
            name = props.model_alias or props.model_path or model_id or "unknown"
            rendered = json.dumps(raw, indent=2)
            return {"ui": {"text": (rendered,)}, "result": (str(name), int(context), rendered)}
        except Exception as exc:
            message = f"Error fetching server props: {type(exc).__name__}: {exc}"
            return {"ui": {"text": (message,)}, "result": ("", 0, "{}")}


NODE_CLASS_MAPPINGS = {"LlamaCppModelInfo": LlamaCppModelInfo}
NODE_DISPLAY_NAME_MAPPINGS = {"LlamaCppModelInfo": "llama.cpp Model Info"}
