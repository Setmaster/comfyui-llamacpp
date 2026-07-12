"""Canonical strict llama.cpp generation node."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..generation.contracts import (
    MAX_REQUEST_IMAGE_COUNT,
    ErrorCategory,
    GenerationErrorInfo,
)
from ..generation.execution import (
    RUNNING_MODEL,
    CanonicalGenerationError,
    CanonicalGenerationExecutor,
)
from ..generation.profiles import TaskProfileSnapshot
from ..model_manager import get_local_models
from ..runtime.live_generation import ExecutionIdentity
from .common import collect_images
from .connection import LlamaCppConnectionProfile
from .presentation import NODE_CATEGORIES, NODE_SEARCH_ALIASES, apply_input_presentation
from .schemas import MAX_IMAGES


def _model_choices() -> list[str]:
    try:
        return [RUNNING_MODEL, *get_local_models()]
    except (OSError, ValueError):
        # Model discovery is also available through the passive runtime route.
        # A catalog problem must not prevent the node pack from registering.
        return [RUNNING_MODEL]


def _option_group(options: dict[str, Any]) -> dict[str, Any]:
    # Keep construction readable; central presentation metadata decides the
    # exact collapsed set after the complete schema exists.
    return dict(options)


def _interrupt_check() -> bool:
    try:
        import comfy.model_management as model_management
    except ImportError:
        return False
    model_management.throw_exception_if_processing_interrupted()
    return False


def _execution_identity(
    unique_id: object,
    dynprompt: Any,
    extra_pnginfo: Any = None,
) -> ExecutionIdentity | None:
    """Capture current Comfy execution identity without requiring Comfy at import time."""

    context = None
    try:
        from comfy_execution.utils import get_executing_context

        context = get_executing_context()
    except Exception:
        pass

    node_id = getattr(context, "node_id", None) or unique_id
    if node_id is None:
        return None
    prompt_id = getattr(context, "prompt_id", None)
    list_index = getattr(context, "list_index", None)
    display_node_id = node_id
    real_node_id = node_id
    parent_node_id = None
    if dynprompt is not None:
        try:
            display_node_id = dynprompt.get_display_node_id(node_id)
            real_node_id = dynprompt.get_real_node_id(node_id)
            parent_node_id = dynprompt.get_parent_node_id(node_id)
        except Exception:
            # Dynamic identity is additive observability. The exact current node
            # still provides a safe fallback if a third-party graph wrapper does
            # not expose current DynamicPrompt helpers.
            display_node_id = node_id
            real_node_id = node_id
            parent_node_id = None

    client_id = None
    try:
        from server import PromptServer

        client_id = getattr(PromptServer.instance, "client_id", None)
    except Exception:
        pass

    workflow_id = None
    if isinstance(extra_pnginfo, Mapping):
        workflow = extra_pnginfo.get("workflow")
        candidate = workflow.get("id") if isinstance(workflow, Mapping) else None
        if isinstance(candidate, str) and candidate:
            workflow_id = candidate

    try:
        return ExecutionIdentity.create(
            prompt_id=str(prompt_id) if prompt_id else None,
            node_id=node_id,
            display_node_id=display_node_id,
            real_node_id=real_node_id,
            parent_node_id=parent_node_id,
            list_index=list_index,
            workflow_id=workflow_id,
            client_id=str(client_id) if client_id else None,
        )
    except (TypeError, ValueError):
        return None


class LlamaCppGenerate:
    """One strict text, vision, and constrained-generation surface."""

    DESCRIPTION = (
        "Canonical local llama.cpp generation with strict failures, live progress, "
        "profiles, exact router targeting, and optional terminal release."
    )
    CATEGORY = NODE_CATEGORIES["LlamaCppGenerate"]
    SEARCH_ALIASES = NODE_SEARCH_ALIASES["LlamaCppGenerate"]
    RETURN_TYPES = ("STRING", "STRING", "LLAMACPP_GENERATION_RESULT")
    RETURN_NAMES = ("response", "thinking", "result")
    OUTPUT_TOOLTIPS = (
        "Final generated response, or explicitly accepted marked partial response.",
        "Reasoning content reported separately by compatible models.",
        "Versioned JSON-safe generation and release metadata.",
    )
    FUNCTION = "generate"
    OUTPUT_NODE = True
    MAX_IMAGES = MAX_IMAGES

    @classmethod
    def INPUT_TYPES(cls):
        optional: dict[str, Any] = {
            "connection": (
                "LLAMACPP_CONNECTION",
                {"tooltip": "Reusable secret-free local or attached server connection."},
            ),
            "profile": (
                "LLAMACPP_PROFILE",
                {"tooltip": "Portable task-profile snapshot. Controls remain unchanged."},
            ),
            "server_url": (
                "STRING",
                _option_group(
                    {
                        "default": "",
                        "placeholder": "http://127.0.0.1:8080",
                        "tooltip": (
                            "Advanced attached or Start-node URL. Leave empty for the managed "
                            "runtime, and do not combine with Connection."
                        ),
                    }
                ),
            ),
            "model": (
                _model_choices(),
                {
                    "default": RUNNING_MODEL,
                    "tooltip": "Exact router model ID, or the running direct model.",
                },
            ),
            "system_prompt": (
                "STRING",
                {
                    "multiline": True,
                    "default": "",
                    "placeholder": "Optional system prompt...",
                    "tooltip": "An exactly empty value may be filled by the connected profile.",
                },
            ),
            "thinking_mode": (
                ["auto", "off", "on"],
                {
                    "default": "auto",
                    "tooltip": "Auto omits an override; Off and On send an explicit request.",
                },
            ),
            "max_tokens": (
                "INT",
                {
                    "default": 2048,
                    "min": 1,
                    "max": 1_048_576,
                    "step": 64,
                    "tooltip": "Maximum generated tokens.",
                },
            ),
            "sampling_mode": (
                ["default", "custom"],
                {
                    "default": "default",
                    "tooltip": "Default delegates samplers to the server; Custom sends all controls.",
                },
            ),
            "temperature": (
                "FLOAT",
                _option_group(
                    {
                        "default": 0.7,
                        "min": 0.0,
                        "max": 2.0,
                        "step": 0.05,
                        "tooltip": "Custom sampling randomness.",
                    }
                ),
            ),
            "top_p": (
                "FLOAT",
                _option_group(
                    {
                        "default": 0.9,
                        "min": 0.0,
                        "max": 1.0,
                        "step": 0.05,
                        "tooltip": "Custom cumulative-probability cutoff.",
                    }
                ),
            ),
            "top_k": (
                "INT",
                _option_group(
                    {
                        "default": 40,
                        "min": 0,
                        "max": 1_000_000,
                        "step": 1,
                        "tooltip": "Custom top-K token cutoff; zero disables it.",
                    }
                ),
            ),
            "min_p": (
                "FLOAT",
                _option_group(
                    {
                        "default": 0.05,
                        "min": 0.0,
                        "max": 1.0,
                        "step": 0.01,
                        "tooltip": "Custom probability floor relative to the best token.",
                    }
                ),
            ),
            "repeat_penalty": (
                "FLOAT",
                _option_group(
                    {
                        "default": 1.1,
                        "min": 1.0,
                        "max": 2.0,
                        "step": 0.05,
                        "tooltip": "Custom penalty for recently repeated tokens.",
                    }
                ),
            ),
            "presence_penalty": (
                "FLOAT",
                _option_group(
                    {
                        "default": 0.0,
                        "min": -2.0,
                        "max": 2.0,
                        "step": 0.1,
                        "tooltip": "Custom penalty for tokens that have appeared at least once.",
                    }
                ),
            ),
            "frequency_penalty": (
                "FLOAT",
                _option_group(
                    {
                        "default": 0.0,
                        "min": -2.0,
                        "max": 2.0,
                        "step": 0.1,
                        "tooltip": "Custom penalty proportional to prior token frequency.",
                    }
                ),
            ),
            "seed": (
                "INT",
                {
                    "default": 0,
                    "min": 0,
                    "max": 0x7FFFFFFF,
                    "control_after_generate": True,
                    "tooltip": "Fixed or Comfy-controlled random seed.",
                },
            ),
            "cache_prompt": (
                "BOOLEAN",
                _option_group(
                    {
                        "default": False,
                        "tooltip": "Reuse a matching llama.cpp prompt-prefix KV cache.",
                    }
                ),
            ),
            "stop_sequences": (
                "STRING",
                _option_group(
                    {
                        "multiline": True,
                        "default": "",
                        "placeholder": "One per line, JSON list, or legacy comma list",
                        "tooltip": "Stop sequences. JSON arrays preserve commas and whitespace.",
                    }
                ),
            ),
            "api_key_env": (
                "STRING",
                _option_group(
                    {
                        "default": "LLAMACPP_API_KEY",
                        "tooltip": (
                            "Key variable: LLAMACPP_API_KEY or "
                            "LLAMACPP_API_KEY_<UPPERCASE_SUFFIX>. The key itself is never saved."
                        ),
                    }
                ),
            ),
            "verify_tls": (
                "BOOLEAN",
                _option_group({"default": True, "tooltip": "Verify HTTPS certificates."}),
            ),
            "request_timeout": (
                "INT",
                _option_group(
                    {
                        "default": 300,
                        "min": 1,
                        "max": 86_400,
                        "step": 1,
                        "tooltip": "Overall request and terminal-release wait deadline.",
                    }
                ),
            ),
            "image_amount": (
                "INT",
                {
                    "default": 0,
                    "min": 0,
                    "max": MAX_IMAGES,
                    "step": 1,
                    "tooltip": "Number of IMAGE sockets to expose, from zero through ten.",
                },
            ),
            "include_image_batch": (
                "BOOLEAN",
                _option_group(
                    {
                        "default": False,
                        "tooltip": "Send every frame from each connected Comfy IMAGE batch.",
                    }
                ),
            ),
            "release_after_generation": (
                "BOOLEAN",
                {
                    "default": False,
                    "tooltip": (
                        "Wait for terminal managed runtime/model release before exposing outputs. "
                        "Unavailable for attached endpoints."
                    ),
                },
            ),
            "partial_output_policy": (
                ["raise_error", "return_marked_partial"],
                {
                    "default": "raise_error",
                    "tooltip": "Partial output raises unless explicitly allowed and marked in result.",
                },
            ),
        }
        optional.update(
            {
                f"image_{index}": (
                    "IMAGE",
                    {"tooltip": f"Optional image {index}; visibility follows Image Amount."},
                )
                for index in range(1, MAX_IMAGES + 1)
            }
        )
        optional.update(
            {
                "structured_output": (
                    "STRUCTURED_OUTPUT",
                    _option_group(
                        {"tooltip": "Optional JSON, JSON Schema, or grammar constraint."}
                    ),
                ),
                "token_ban": (
                    "LOGIT_BIAS",
                    _option_group({"tooltip": "Optional token bans from llama.cpp Token Ban."}),
                ),
            }
        )
        schema = {
            "required": {
                "prompt": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": "",
                        "placeholder": "Enter your prompt here...",
                        "tooltip": "User prompt sent through the connected profile snapshot.",
                    },
                )
            },
            "optional": optional,
            "hidden": {
                "unique_id": "UNIQUE_ID",
                "dynprompt": "DYNPROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO",
            },
        }
        return apply_input_presentation("LlamaCppGenerate", schema)

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        del kwargs
        return float("nan")

    def generate(
        self,
        prompt: str,
        connection: LlamaCppConnectionProfile | None = None,
        profile: TaskProfileSnapshot | None = None,
        server_url: str = "",
        model: str = RUNNING_MODEL,
        system_prompt: str = "",
        thinking_mode: str = "auto",
        max_tokens: int = 2048,
        sampling_mode: str = "default",
        temperature: float = 0.7,
        top_p: float = 0.9,
        top_k: int = 40,
        min_p: float = 0.05,
        repeat_penalty: float = 1.1,
        presence_penalty: float = 0.0,
        frequency_penalty: float = 0.0,
        seed: int = 0,
        cache_prompt: bool = False,
        stop_sequences: str = "",
        api_key_env: str = "LLAMACPP_API_KEY",
        verify_tls: bool = True,
        request_timeout: int = 300,
        image_amount: int = 0,
        include_image_batch: bool = False,
        release_after_generation: bool = False,
        partial_output_policy: str = "raise_error",
        image_1: Any = None,
        image_2: Any = None,
        image_3: Any = None,
        image_4: Any = None,
        image_5: Any = None,
        image_6: Any = None,
        image_7: Any = None,
        image_8: Any = None,
        image_9: Any = None,
        image_10: Any = None,
        structured_output: Any = None,
        token_ban: Any = None,
        unique_id: object = None,
        dynprompt: Any = None,
        extra_pnginfo: Any = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del kwargs
        if type(image_amount) is not int or not 0 <= image_amount <= MAX_IMAGES:
            raise CanonicalGenerationError(
                GenerationErrorInfo(
                    ErrorCategory.INVALID_REQUEST,
                    f"image_amount must be an integer between 0 and {MAX_IMAGES}",
                )
            )
        image_inputs = {
            f"image_{index}": value
            for index, value in enumerate(
                (
                    image_1,
                    image_2,
                    image_3,
                    image_4,
                    image_5,
                    image_6,
                    image_7,
                    image_8,
                    image_9,
                    image_10,
                ),
                start=1,
            )
        }
        try:
            images = collect_images(
                image_amount,
                image_inputs,
                include_batch=include_image_batch,
                maximum_images=MAX_REQUEST_IMAGE_COUNT,
                interrupt_check=_interrupt_check,
            )
        except Exception as exc:
            raise CanonicalGenerationError(
                GenerationErrorInfo(
                    ErrorCategory.INVALID_REQUEST,
                    f"failed to process image input ({type(exc).__name__})"[:4096],
                )
            ) from None

        response, thinking, result = CanonicalGenerationExecutor().generate(
            prompt,
            connection=connection,
            profile=profile,
            server_url=server_url,
            model=model,
            system_prompt=system_prompt,
            thinking_mode=thinking_mode,
            max_tokens=max_tokens,
            sampling_mode=sampling_mode,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            min_p=min_p,
            repeat_penalty=repeat_penalty,
            presence_penalty=presence_penalty,
            frequency_penalty=frequency_penalty,
            seed=seed,
            cache_prompt=cache_prompt,
            stop_sequences=stop_sequences,
            api_key_env=api_key_env,
            verify_tls=verify_tls,
            request_timeout=request_timeout,
            images=images,
            release_after_generation=release_after_generation,
            partial_output_policy=partial_output_policy,
            structured_output=structured_output,
            token_ban=token_ban,
            identity=_execution_identity(unique_id, dynprompt, extra_pnginfo),
            cancel_check=_interrupt_check,
        )
        return {
            # Current Comfy's jobs API recognizes ui.text as native text output
            # and promotes it into App Mode's text preview. A custom result-item
            # key would instead become an unknown, non-previewable media type.
            "ui": {"text": (response,)},
            "result": (response, thinking, result),
        }


NODE_CLASS_MAPPINGS = {"LlamaCppGenerate": LlamaCppGenerate}
NODE_DISPLAY_NAME_MAPPINGS = {"LlamaCppGenerate": "llama.cpp Generate"}

__all__ = [
    "LlamaCppGenerate",
    "NODE_CLASS_MAPPINGS",
    "NODE_DISPLAY_NAME_MAPPINGS",
]
