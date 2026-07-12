"""Ordered V1 input schemas shared by the prompt nodes.

Primitive widget order is a saved-workflow contract. Do not insert fields into
the legacy prefixes below. New widgets belong after the final legacy widget.
"""

from __future__ import annotations

from typing import Any

from ..model_manager import get_local_models
from .presentation import apply_input_presentation

MAX_IMAGES = 10
RUNNING_MODEL = "(use running model)"


def _prompt() -> tuple[str, dict[str, Any]]:
    return (
        "STRING",
        {
            "multiline": True,
            "default": "",
            "placeholder": "Enter your prompt here...",
            "tooltip": "The user prompt to send to the LLM",
        },
    )


def _image_amount() -> tuple[str, dict[str, Any]]:
    return (
        "INT",
        {
            "default": 2,
            "min": 0,
            "max": MAX_IMAGES,
            "step": 1,
            "tooltip": "Number of image input slots to show",
        },
    )


def _common_widgets(*, penalties_are_legacy: bool) -> dict[str, Any]:
    models = [RUNNING_MODEL, *get_local_models()]
    widgets: dict[str, Any] = {
        "model": (
            models,
            {
                "default": RUNNING_MODEL,
                "tooltip": "Model for router mode, or the running direct model.",
            },
        ),
        "server_url": (
            "STRING",
            {
                "default": "",
                "placeholder": "http://127.0.0.1:8080",
                "tooltip": (
                    "Leave empty to use the server owned by this node pack. Attached "
                    "endpoints are never implicitly stopped."
                ),
            },
        ),
        "system_prompt": (
            "STRING",
            {
                "multiline": True,
                "default": "",
                "placeholder": "Optional system prompt...",
                "tooltip": "System prompt that defines model behavior.",
            },
        ),
        "enable_thinking": (
            "BOOLEAN",
            {
                "default": True,
                "tooltip": "Request thinking/reasoning from compatible models.",
            },
        ),
        "max_tokens": (
            "INT",
            {
                "default": 2048,
                "min": 1,
                "max": 131072,
                "step": 64,
                "tooltip": "Maximum number of tokens to generate.",
            },
        ),
        "temperature": (
            "FLOAT",
            {
                "default": 0.7,
                "min": 0.0,
                "max": 2.0,
                "step": 0.05,
                "tooltip": "Sampling randomness. Lower values are more deterministic.",
            },
        ),
        "top_p": (
            "FLOAT",
            {
                "default": 0.9,
                "min": 0.0,
                "max": 1.0,
                "step": 0.05,
                "tooltip": "Keep tokens within this cumulative probability mass.",
            },
        ),
        "top_k": (
            "INT",
            {
                "default": 40,
                "min": 0,
                "max": 200,
                "step": 1,
                "tooltip": "Sample from the top K tokens. 0 disables top-k filtering.",
            },
        ),
        "min_p": (
            "FLOAT",
            {
                "default": 0.05,
                "min": 0.0,
                "max": 1.0,
                "step": 0.01,
                "tooltip": "Discard tokens below this probability relative to the best token.",
            },
        ),
        "repeat_penalty": (
            "FLOAT",
            {
                "default": 1.1,
                "min": 1.0,
                "max": 2.0,
                "step": 0.05,
                "tooltip": "Penalize recently repeated tokens. 1.0 disables the penalty.",
            },
        ),
    }
    if penalties_are_legacy:
        widgets.update(_penalty_widgets())
    widgets.update(
        {
            "seed": (
                "INT",
                {"default": 0, "min": 0, "max": 0x7FFFFFFF, "tooltip": "Random seed"},
            ),
            "keep_context": (
                "BOOLEAN",
                {
                    "default": False,
                    "tooltip": "Reuse a matching prompt-prefix KV cache. This is not chat history.",
                },
            ),
            "enable_chaining": (
                "BOOLEAN",
                {
                    "default": False,
                    "tooltip": "Compatibility toggle. A connected trigger already controls ordering.",
                },
            ),
        }
    )
    return widgets


def _penalty_widgets() -> dict[str, Any]:
    return {
        "presence_penalty": (
            "FLOAT",
            {
                "default": 0.0,
                "min": -2.0,
                "max": 2.0,
                "step": 0.1,
                "tooltip": "Penalize tokens that have appeared at least once.",
            },
        ),
        "frequency_penalty": (
            "FLOAT",
            {
                "default": 0.0,
                "min": -2.0,
                "max": 2.0,
                "step": 0.1,
                "tooltip": "Penalize tokens in proportion to how often they appeared.",
            },
        ),
    }


def _new_generation_widgets() -> dict[str, Any]:
    return {
        "stop_sequences": (
            "STRING",
            {
                "multiline": True,
                "default": "",
                "placeholder": "One per line, JSON list, or legacy comma list",
                "tooltip": "Stop sequences. JSON arrays preserve commas and whitespace.",
            },
        ),
        "api_key_env": (
            "STRING",
            {
                "default": "LLAMACPP_API_KEY",
                "tooltip": "Environment variable containing the API key. The secret is not serialized.",
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
                "tooltip": "Overall generation deadline in seconds.",
            },
        ),
    }


def _trigger() -> tuple[str, dict[str, Any]]:
    return ("*", {"tooltip": "Optional dependency input used to sequence execution."})


def _image_inputs() -> dict[str, Any]:
    return {
        f"image_{index}": (
            "IMAGE",
            {"tooltip": f"Optional image {index}. Visibility follows image_amount."},
        )
        for index in range(1, MAX_IMAGES + 1)
    }


def basic_prompt_inputs() -> dict[str, dict[str, Any]]:
    optional = _common_widgets(penalties_are_legacy=False)
    # The released full optional sequence ends with trigger.
    optional["trigger"] = _trigger()
    # Basic acquired these after its released legacy prefix.
    optional.update(_penalty_widgets())
    optional.update(_new_generation_widgets())
    optional["connection"] = (
        "LLAMACPP_CONNECTION",
        {"tooltip": "Optional reusable local or remote connection profile."},
    )
    schema = {"required": {"prompt": _prompt()}, "optional": optional}
    return apply_input_presentation("LlamaCppBasicPrompt", schema)


def advanced_prompt_inputs() -> dict[str, dict[str, Any]]:
    optional = _common_widgets(penalties_are_legacy=True)
    # The released full optional sequence ends with trigger.
    optional["trigger"] = _trigger()
    optional.update(_new_generation_widgets())
    optional["include_image_batch"] = (
        "BOOLEAN",
        {
            "default": False,
            "tooltip": "Send every image in each Comfy IMAGE batch. Off preserves legacy first-image behavior.",
        },
    )
    optional.update(_image_inputs())
    optional["connection"] = (
        "LLAMACPP_CONNECTION",
        {"tooltip": "Optional reusable local or remote connection profile."},
    )
    schema = {
        "required": {"prompt": _prompt(), "image_amount": _image_amount()},
        "optional": optional,
    }
    return apply_input_presentation("LlamaCppAdvPrompt", schema)


def advanced_pp_prompt_inputs(template_names: list[str]) -> dict[str, dict[str, Any]]:
    optional = _common_widgets(penalties_are_legacy=True)
    # These sockets and this widget are part of the released ADV++ contract.
    optional["trigger"] = _trigger()
    optional["token_ban"] = (
        "LOGIT_BIAS",
        {"tooltip": "Token ban list from a llama.cpp Token Ban node."},
    )
    optional["enable_token_ban"] = (
        "BOOLEAN",
        {"default": True, "tooltip": "Enable or disable the connected token ban list."},
    )
    # New primitive widgets begin only after enable_token_ban.
    optional.update(_new_generation_widgets())
    optional["include_image_batch"] = (
        "BOOLEAN",
        {"default": False, "tooltip": "Send every image in each connected IMAGE batch."},
    )
    optional.update(_image_inputs())
    optional["structured_output"] = (
        "STRUCTURED_OUTPUT",
        {"tooltip": "JSON schema, JSON object, or GBNF constraint."},
    )
    optional["connection"] = (
        "LLAMACPP_CONNECTION",
        {"tooltip": "Optional reusable local or remote connection profile."},
    )
    schema = {
        "required": {
            "template": (
                template_names,
                {
                    "default": "Empty",
                    "tooltip": (
                        "Fill exact-empty prompt fields from the selected bundled template. "
                        "Existing text is preserved; use Replace / Reset to overwrite it."
                    ),
                },
            ),
            "prompt": _prompt(),
            "image_amount": _image_amount(),
        },
        "optional": optional,
    }
    return apply_input_presentation("LlamaCppAdvPPPrompt", schema)
