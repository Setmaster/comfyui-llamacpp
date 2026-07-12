"""Reusable, secret-free llama-server connection profile node."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from .presentation import NODE_CATEGORIES, NODE_SEARCH_ALIASES, apply_input_presentation


@dataclass(frozen=True, slots=True)
class LlamaCppConnectionProfile:
    server_url: str = ""
    model: str = ""
    api_key_env: str = "LLAMACPP_API_KEY"
    verify_tls: bool = True
    request_timeout: int = 300

    SCHEMA_VERSION: ClassVar[int] = 1
    _SERIALIZED_KEYS: ClassVar[frozenset[str]] = frozenset(
        {
            "schema_version",
            "server_url",
            "model",
            "api_key_env",
            "verify_tls",
            "request_timeout",
        }
    )

    def as_dict(self) -> dict[str, Any]:
        """Return the complete secret-free workflow connection snapshot."""

        return {
            "schema_version": self.SCHEMA_VERSION,
            "server_url": self.server_url,
            "model": self.model,
            "api_key_env": self.api_key_env,
            "verify_tls": self.verify_tls,
            "request_timeout": self.request_timeout,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> LlamaCppConnectionProfile:
        """Restore a versioned snapshot without resolving its environment secret."""

        if not isinstance(value, Mapping):
            raise TypeError("connection snapshot must be an object")
        actual = set(value)
        if actual != set(cls._SERIALIZED_KEYS):
            missing = sorted(cls._SERIALIZED_KEYS - actual, key=repr)
            extra = sorted(actual - cls._SERIALIZED_KEYS, key=repr)
            details = []
            if missing:
                details.append(f"missing {missing}")
            if extra:
                details.append(f"unexpected {extra}")
            raise ValueError(f"connection snapshot has invalid keys ({'; '.join(details)})")
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise ValueError("connection snapshot schema_version must be integer 1")
        for field_name in ("server_url", "model", "api_key_env"):
            if not isinstance(value[field_name], str):
                raise TypeError(f"connection snapshot {field_name} must be a string")
        if type(value["verify_tls"]) is not bool:
            raise TypeError("connection snapshot verify_tls must be a Boolean")
        request_timeout = value["request_timeout"]
        if type(request_timeout) is not int:
            raise TypeError("connection snapshot request_timeout must be an integer")
        if not 1 <= request_timeout <= 86_400:
            raise ValueError("connection snapshot request_timeout must be between 1 and 86400")
        return cls(
            server_url=value["server_url"],
            model=value["model"],
            api_key_env=value["api_key_env"],
            verify_tls=value["verify_tls"],
            request_timeout=request_timeout,
        )


class LlamaCppConnection:
    DESCRIPTION = (
        "Creates a reusable llama-server connection profile while keeping API-key values "
        "out of saved workflows."
    )
    CATEGORY = NODE_CATEGORIES["LlamaCppConnection"]
    SEARCH_ALIASES = NODE_SEARCH_ALIASES["LlamaCppConnection"]
    RETURN_TYPES = ("LLAMACPP_CONNECTION",)
    RETURN_NAMES = ("connection",)
    OUTPUT_TOOLTIPS = ("Reusable secret-free llama-server connection profile.",)
    FUNCTION = "create_connection"

    @classmethod
    def INPUT_TYPES(cls):
        schema = {
            "required": {
                "server_url": (
                    "STRING",
                    {
                        "default": "",
                        "placeholder": "empty = server owned by this pack",
                        "tooltip": (
                            "Local or remote llama-server URL, or empty for the managed server. "
                            "Attached endpoints are never implicitly stopped."
                        ),
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
                "verify_tls": (
                    "BOOLEAN",
                    {"default": True, "tooltip": "Verify HTTPS certificates."},
                ),
                "request_timeout": (
                    "INT",
                    {
                        "default": 300,
                        "min": 1,
                        "max": 86400,
                        "step": 1,
                        "tooltip": "Overall request deadline in seconds.",
                    },
                ),
            },
        }
        return apply_input_presentation("LlamaCppConnection", schema)

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
