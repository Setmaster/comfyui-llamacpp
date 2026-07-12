"""Tokenize text through a local llama-server."""

from __future__ import annotations

import json

from ..runtime.client import LlamaServerClient
from ..server_manager import get_server_manager
from .connection import LlamaCppConnectionProfile
from .presentation import NODE_CATEGORIES, NODE_SEARCH_ALIASES, apply_input_presentation


class LlamaCppTokenCount:
    DESCRIPTION = (
        "Tokenizes text through llama-server and returns the token count plus optional "
        "token or piece details."
    )
    CATEGORY = NODE_CATEGORIES["LlamaCppTokenCount"]
    SEARCH_ALIASES = NODE_SEARCH_ALIASES["LlamaCppTokenCount"]
    RETURN_TYPES = ("INT", "STRING")
    RETURN_NAMES = ("token_count", "tokens_json")
    OUTPUT_TOOLTIPS = (
        "Number of tokens returned by llama-server.",
        "Token IDs or token-piece details as JSON.",
    )
    FUNCTION = "count_tokens"

    @classmethod
    def INPUT_TYPES(cls):
        schema = {
            "required": {
                "text": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": "",
                        "placeholder": "Text to tokenize",
                        "tooltip": "Text to tokenize with the selected llama-server model.",
                    },
                )
            },
            "optional": {
                "server_url": (
                    "STRING",
                    {
                        "default": "",
                        "tooltip": "llama-server URL, or empty for the managed runtime.",
                    },
                ),
                "model": (
                    "STRING",
                    {"default": "", "tooltip": "Optional exact router model ID."},
                ),
                "add_special": (
                    "BOOLEAN",
                    {"default": False, "tooltip": "Add the model's special boundary tokens."},
                ),
                "parse_special": (
                    "BOOLEAN",
                    {"default": True, "tooltip": "Recognize special-token text in the input."},
                ),
                "with_pieces": (
                    "BOOLEAN",
                    {"default": False, "tooltip": "Ask llama-server to include token pieces."},
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
                        "tooltip": "Tokenization deadline in seconds.",
                    },
                ),
                "connection": (
                    "LLAMACPP_CONNECTION",
                    {"tooltip": "Optional reusable connection profile."},
                ),
            },
        }
        return apply_input_presentation(
            "LlamaCppTokenCount", schema, display_names={"text": "Text to Tokenize"}
        )

    def count_tokens(
        self,
        text: str,
        server_url: str = "",
        model: str = "",
        add_special: bool = False,
        parse_special: bool = True,
        with_pieces: bool = False,
        api_key_env: str = "LLAMACPP_API_KEY",
        verify_tls: bool = True,
        request_timeout: int = 30,
        connection: LlamaCppConnectionProfile | None = None,
    ):
        if not text:
            return 0, "[]"
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
                result = client.tokenize(
                    text,
                    model=model_id,
                    add_special=add_special,
                    parse_special=parse_special,
                    with_pieces=with_pieces,
                    timeout=request_timeout,
                )
            return result.count, json.dumps(result.raw.get("tokens", list(result.tokens)))
        except Exception as exc:
            print(f"[llama.cpp] Tokenization error: {type(exc).__name__}: {exc}")
            return 0, "[]"


NODE_CLASS_MAPPINGS = {"LlamaCppTokenCount": LlamaCppTokenCount}
NODE_DISPLAY_NAME_MAPPINGS = {"LlamaCppTokenCount": "llama.cpp Token Count"}
