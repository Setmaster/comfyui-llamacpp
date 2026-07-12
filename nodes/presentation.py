"""Shared presentation metadata for the registered ComfyUI nodes.

This module is intentionally limited to labels, search aliases, categories, and
advanced-widget hints.  Runtime input names, ordering, types, and defaults remain
owned by each node's schema.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

CATEGORY_RUNTIME = "LlamaCpp/Runtime"
CATEGORY_ROUTER = "LlamaCpp/Router"
CATEGORY_GENERATE = "LlamaCpp/Generate"
CATEGORY_UTILITIES = "LlamaCpp/Utilities"

NODE_CATEGORIES = {
    "StartLlamaCppServer": CATEGORY_RUNTIME,
    "StopLlamaCppServer": CATEGORY_RUNTIME,
    "LlamaCppServerStatus": CATEGORY_RUNTIME,
    "LlamaCppReleaseRuntime": CATEGORY_RUNTIME,
    "LlamaCppConnection": CATEGORY_RUNTIME,
    "StartLlamaCppRouter": CATEGORY_ROUTER,
    "LlamaCppListModels": CATEGORY_ROUTER,
    "LlamaCppLoadModel": CATEGORY_ROUTER,
    "LlamaCppUnloadModel": CATEGORY_ROUTER,
    "LlamaCppBasicPrompt": CATEGORY_GENERATE,
    "LlamaCppAdvPrompt": CATEGORY_GENERATE,
    "LlamaCppAdvPPPrompt": CATEGORY_GENERATE,
    "LlamaCppPromptOutput": CATEGORY_UTILITIES,
    "LlamaCppTokenBan": CATEGORY_UTILITIES,
    "LlamaCppTokenCount": CATEGORY_UTILITIES,
    "LlamaCppModelInfo": CATEGORY_UTILITIES,
    "LlamaCppStructuredOutput": CATEGORY_UTILITIES,
}

NODE_SEARCH_ALIASES = {
    "StartLlamaCppServer": [
        "llm",
        "gguf",
        "local llm",
        "llama-server",
        "run gguf",
        "vlm server",
    ],
    "StopLlamaCppServer": ["stop llm", "stop llama-server", "shut down llm"],
    "LlamaCppServerStatus": [
        "llm status",
        "server status",
        "runtime diagnostics",
        "setup diagnostics",
        "llama-server logs",
    ],
    "LlamaCppReleaseRuntime": [
        "free vram",
        "release vram",
        "free gpu memory",
        "unload llm",
        "offload model",
    ],
    "LlamaCppConnection": [
        "llm connection",
        "llama-server url",
        "attached server",
        "remote llama-server",
    ],
    "StartLlamaCppRouter": [
        "llm router",
        "gguf router",
        "multi-model server",
        "llama-server router",
    ],
    "LlamaCppListModels": [
        "router models",
        "model catalog",
        "list gguf models",
        "refresh models",
    ],
    "LlamaCppLoadModel": ["load llm", "load gguf", "router load model"],
    "LlamaCppUnloadModel": [
        "unload llm",
        "unload gguf",
        "router unload model",
        "free model vram",
    ],
    "LlamaCppBasicPrompt": [
        "llm",
        "generate text",
        "text generation",
        "local prompting",
    ],
    "LlamaCppAdvPrompt": [
        "vlm",
        "vision language model",
        "image to text",
        "image to prompt",
        "describe image",
        "multimodal llm",
    ],
    "LlamaCppAdvPPPrompt": [
        "prompt enhancer",
        "enhance prompt",
        "prompt template",
        "vlm",
        "image to prompt",
        "structured generation",
    ],
    "LlamaCppPromptOutput": [
        "llm output",
        "text preview",
        "display text",
        "show response",
        "markdown to plaintext",
    ],
    "LlamaCppTokenBan": [
        "ban tokens",
        "logit bias",
        "block tokens",
        "forbidden tokens",
    ],
    "LlamaCppTokenCount": [
        "count tokens",
        "tokenizer",
        "prompt length",
        "context budget",
    ],
    "LlamaCppModelInfo": [
        "model metadata",
        "gguf info",
        "context length",
        "server properties",
    ],
    "LlamaCppStructuredOutput": [
        "json",
        "json schema",
        "structured output",
        "gbnf",
        "grammar",
        "constrained generation",
    ],
}

# Primitive widgets listed here start collapsed in supporting ComfyUI frontends.
# Socket inputs are deliberately excluded so links remain discoverable.
NODE_ADVANCED_INPUTS = {
    "StartLlamaCppServer": frozenset(
        {
            "gpu_layers",
            "main_gpu",
            "port",
            "threads",
            "batch_size",
            "flash_attention",
            "timeout",
            "host",
            "tensor_split",
            "no_mmap",
            "flash_attention_mode",
            "sleep_idle_seconds",
            "api_key_file",
            "api_key_env",
            "media_path",
            "fit_mode",
            "extra_args",
        }
    ),
    "StopLlamaCppServer": frozenset(),
    "LlamaCppServerStatus": frozenset(),
    "LlamaCppReleaseRuntime": frozenset(),
    "LlamaCppConnection": frozenset({"api_key_env", "verify_tls", "request_timeout"}),
    "StartLlamaCppRouter": frozenset(
        {
            "gpu_layers",
            "main_gpu",
            "port",
            "threads",
            "batch_size",
            "flash_attention",
            "models_autoload",
            "timeout",
            "host",
            "tensor_split",
            "no_mmap",
            "flash_attention_mode",
            "sleep_idle_seconds",
            "api_key_file",
            "api_key_env",
            "media_path",
            "fit_mode",
            "extra_args",
        }
    ),
    "LlamaCppListModels": frozenset({"reload_catalog"}),
    "LlamaCppLoadModel": frozenset({"operation_timeout"}),
    "LlamaCppUnloadModel": frozenset({"operation_timeout"}),
    "LlamaCppBasicPrompt": frozenset(
        {
            "server_url",
            "top_p",
            "top_k",
            "min_p",
            "repeat_penalty",
            "seed",
            "keep_context",
            "enable_chaining",
            "presence_penalty",
            "frequency_penalty",
            "stop_sequences",
            "api_key_env",
            "verify_tls",
            "request_timeout",
        }
    ),
    "LlamaCppAdvPrompt": frozenset(
        {
            "server_url",
            "top_p",
            "top_k",
            "min_p",
            "repeat_penalty",
            "presence_penalty",
            "frequency_penalty",
            "seed",
            "keep_context",
            "enable_chaining",
            "stop_sequences",
            "api_key_env",
            "verify_tls",
            "request_timeout",
            "include_image_batch",
        }
    ),
    "LlamaCppAdvPPPrompt": frozenset(
        {
            "server_url",
            "top_p",
            "top_k",
            "min_p",
            "repeat_penalty",
            "presence_penalty",
            "frequency_penalty",
            "seed",
            "keep_context",
            "enable_chaining",
            "enable_token_ban",
            "stop_sequences",
            "api_key_env",
            "verify_tls",
            "request_timeout",
            "include_image_batch",
        }
    ),
    "LlamaCppPromptOutput": frozenset({"plaintext"}),
    "LlamaCppTokenBan": frozenset(),
    "LlamaCppTokenCount": frozenset(
        {
            "add_special",
            "parse_special",
            "with_pieces",
            "api_key_env",
            "verify_tls",
            "request_timeout",
        }
    ),
    "LlamaCppModelInfo": frozenset({"api_key_env", "verify_tls", "request_timeout"}),
    "LlamaCppStructuredOutput": frozenset({"schema_name", "strict"}),
}

INPUT_DISPLAY_NAMES = {
    "prompt": "Prompt",
    "image_amount": "Image Inputs",
    "model": "Model",
    "model_name": "Model",
    "server_url": "Server URL",
    "system_prompt": "System Prompt",
    "enable_thinking": "Thinking",
    "max_tokens": "Max Tokens",
    "temperature": "Temperature",
    "top_p": "Top P",
    "top_k": "Top K",
    "min_p": "Min P",
    "repeat_penalty": "Repeat Penalty",
    "presence_penalty": "Presence Penalty",
    "frequency_penalty": "Frequency Penalty",
    "seed": "Seed",
    "keep_context": "Reuse Prompt Prefix Cache",
    "enable_chaining": "Legacy Chaining (Compatibility)",
    "trigger": "Trigger",
    "stop_sequences": "Stop Sequences",
    "api_key_env": "API Key Environment Variable",
    "verify_tls": "Verify TLS Certificates",
    "request_timeout": "Request Timeout (Seconds)",
    "include_image_batch": "Include Full Image Batches",
    "connection": "Connection",
    "template": "Template",
    "token_ban": "Token Ban",
    "enable_token_ban": "Enable Token Ban",
    "structured_output": "Structured Output",
    "context_size": "Context Size",
    "gpu_layers": "GPU Layers",
    "main_gpu": "Primary GPU",
    "port": "Port",
    "threads": "CPU Threads",
    "batch_size": "Batch Size",
    "flash_attention": "Legacy Flash Attention",
    "timeout": "Startup Timeout (Seconds)",
    "binary_path": "llama-server Binary",
    "host": "Bind Address",
    "tensor_split": "Tensor Split",
    "no_mmap": "Disable Memory Mapping",
    "flash_attention_mode": "Flash Attention Mode",
    "mmproj": "Vision Projector",
    "sleep_idle_seconds": "Idle Sleep (Seconds)",
    "api_key_file": "API Key File",
    "media_path": "Media Directory",
    "fit_mode": "Fit to Device Memory",
    "unload_comfy_models_before_start": "Unload Comfy Models Before Start",
    "extra_args": "Extra Arguments",
    "models_max": "Maximum Loaded Models",
    "models_autoload": "Autoload Models",
    "models_directory": "Models Directory",
    "reload_catalog": "Reload Model Catalog",
    "operation_timeout": "Operation Timeout (Seconds)",
    "text": "Text",
    "plaintext": "Convert to Plaintext",
    "mode": "Constraint Type",
    "constraint": "Schema or Grammar",
    "enable": "Enabled",
    "schema_name": "Schema Name",
    "strict": "Strict JSON Schema",
    "banned_tokens": "Banned Tokens",
    "add_special": "Add Special Tokens",
    "parse_special": "Parse Special Tokens",
    "with_pieces": "Include Token Pieces",
    **{f"image_{index}": f"Image {index}" for index in range(1, 11)},
}


def apply_input_presentation(
    node_id: str,
    schema: dict[str, dict[str, Any]],
    *,
    display_names: Mapping[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Add presentation-only options without changing a schema's structure."""

    advanced = NODE_ADVANCED_INPUTS[node_id]
    overrides = display_names or {}
    seen: set[str] = set()

    for group in ("required", "optional"):
        for input_name, input_spec in schema.get(group, {}).items():
            if len(input_spec) < 2 or not isinstance(input_spec[1], dict):
                raise TypeError(f"{node_id}.{input_name} must have an input options dictionary")
            options = input_spec[1]
            display_name = overrides.get(input_name, INPUT_DISPLAY_NAMES.get(input_name))
            if not display_name:
                raise KeyError(f"No display name is defined for {node_id}.{input_name}")
            options["display_name"] = display_name
            if input_name in advanced:
                options["advanced"] = True
            seen.add(input_name)

    missing_advanced = advanced - seen
    if missing_advanced:
        missing = ", ".join(sorted(missing_advanced))
        raise KeyError(f"Advanced inputs are missing from {node_id}: {missing}")
    return schema
