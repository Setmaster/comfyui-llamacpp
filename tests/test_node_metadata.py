"""User-facing metadata checks for the complete registered node surface."""

from __future__ import annotations

import importlib

import pytest


def _input_options(input_spec) -> dict:
    if len(input_spec) > 1 and isinstance(input_spec[1], dict):
        return input_spec[1]
    return {}


EXPECTED_CATEGORIES = {
    "StartLlamaCppServer": "LlamaCpp/Runtime",
    "StopLlamaCppServer": "LlamaCpp/Runtime",
    "LlamaCppServerStatus": "LlamaCpp/Runtime",
    "LlamaCppReleaseRuntime": "LlamaCpp/Runtime",
    "LlamaCppConnection": "LlamaCpp/Runtime",
    "StartLlamaCppRouter": "LlamaCpp/Router",
    "LlamaCppListModels": "LlamaCpp/Router",
    "LlamaCppLoadModel": "LlamaCpp/Router",
    "LlamaCppUnloadModel": "LlamaCpp/Router",
    "LlamaCppBasicPrompt": "LlamaCpp/Generate",
    "LlamaCppAdvPrompt": "LlamaCpp/Generate",
    "LlamaCppAdvPPPrompt": "LlamaCpp/Generate",
    "LlamaCppPromptOutput": "LlamaCpp/Utilities",
    "LlamaCppTokenBan": "LlamaCpp/Utilities",
    "LlamaCppTokenCount": "LlamaCpp/Utilities",
    "LlamaCppModelInfo": "LlamaCpp/Utilities",
    "LlamaCppStructuredOutput": "LlamaCpp/Utilities",
    "LlamaCppGenerate": "LlamaCpp/Generate",
    "LlamaCppTaskProfile": "LlamaCpp/Generate",
}

EXPECTED_SEARCH_ALIASES = {
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
    "LlamaCppGenerate": [
        "llm",
        "local llm",
        "gguf",
        "vlm",
        "generate text",
        "image understanding",
        "prompt generation",
        "prompt enhancer",
        "structured generation",
    ],
    "LlamaCppTaskProfile": [
        "llm profile",
        "prompt profile",
        "task profile",
        "prompt enhancer profile",
    ],
}

EXPECTED_ADVANCED_INPUTS = {
    "StartLlamaCppServer": {
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
    },
    "StopLlamaCppServer": set(),
    "LlamaCppServerStatus": set(),
    "LlamaCppReleaseRuntime": set(),
    "LlamaCppConnection": {"api_key_env", "verify_tls", "request_timeout"},
    "StartLlamaCppRouter": {
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
    },
    "LlamaCppListModels": {"reload_catalog"},
    "LlamaCppLoadModel": {"operation_timeout"},
    "LlamaCppUnloadModel": {"operation_timeout"},
    "LlamaCppBasicPrompt": {
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
    },
    "LlamaCppAdvPrompt": {
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
    },
    "LlamaCppAdvPPPrompt": {
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
    },
    "LlamaCppPromptOutput": {"plaintext"},
    "LlamaCppTokenBan": set(),
    "LlamaCppTokenCount": {
        "add_special",
        "parse_special",
        "with_pieces",
        "api_key_env",
        "verify_tls",
        "request_timeout",
    },
    "LlamaCppModelInfo": {"api_key_env", "verify_tls", "request_timeout"},
    "LlamaCppStructuredOutput": {"schema_name", "strict"},
    "LlamaCppGenerate": {
        "server_url",
        "stop_sequences",
        "api_key_env",
        "verify_tls",
        "request_timeout",
        "include_image_batch",
        "partial_output_policy",
    },
    "LlamaCppTaskProfile": set(),
}


def test_every_registered_node_has_a_concise_description(node_package):
    assert len(node_package.NODE_CLASS_MAPPINGS) == 19

    for node_id, node_class in node_package.NODE_CLASS_MAPPINGS.items():
        description = getattr(node_class, "DESCRIPTION", None)
        assert isinstance(description, str), f"{node_id} has no DESCRIPTION"
        assert description == description.strip(), f"{node_id} DESCRIPTION has outer whitespace"
        assert 20 <= len(description) <= 240, f"{node_id} DESCRIPTION is not concise"


def test_every_registered_output_has_a_tooltip(node_package):
    for node_id, node_class in node_package.NODE_CLASS_MAPPINGS.items():
        output_tooltips = getattr(node_class, "OUTPUT_TOOLTIPS", None)
        assert isinstance(output_tooltips, tuple), f"{node_id} has no OUTPUT_TOOLTIPS"
        assert len(output_tooltips) == len(node_class.RETURN_TYPES), (
            f"{node_id} output tooltip count does not match RETURN_TYPES"
        )
        assert all(isinstance(value, str) and value.strip() for value in output_tooltips), (
            f"{node_id} has an empty output tooltip"
        )


def test_categories_and_search_aliases_cover_the_registered_surface(node_package):
    presentation = importlib.import_module(f"{node_package.__name__}.nodes.presentation")
    registered = set(node_package.NODE_CLASS_MAPPINGS)

    assert set(presentation.NODE_CATEGORIES) == registered
    assert set(presentation.NODE_SEARCH_ALIASES) == registered
    assert set(presentation.NODE_ADVANCED_INPUTS) == registered
    assert presentation.NODE_CATEGORIES == EXPECTED_CATEGORIES
    assert presentation.NODE_SEARCH_ALIASES == EXPECTED_SEARCH_ALIASES
    assert presentation.NODE_ADVANCED_INPUTS == EXPECTED_ADVANCED_INPUTS
    assert set(presentation.NODE_CATEGORIES.values()) == {
        presentation.CATEGORY_RUNTIME,
        presentation.CATEGORY_ROUTER,
        presentation.CATEGORY_GENERATE,
        presentation.CATEGORY_UTILITIES,
    }

    for node_id, node_class in node_package.NODE_CLASS_MAPPINGS.items():
        aliases = node_class.SEARCH_ALIASES
        assert node_class.CATEGORY == presentation.NODE_CATEGORIES[node_id]
        assert aliases == presentation.NODE_SEARCH_ALIASES[node_id]
        assert aliases and all(isinstance(alias, str) and alias.strip() for alias in aliases)
        assert len({alias.casefold() for alias in aliases}) == len(aliases)


def test_every_registered_input_has_a_display_name_and_exact_advanced_state(node_package):
    presentation = importlib.import_module(f"{node_package.__name__}.nodes.presentation")

    for node_id, node_class in node_package.NODE_CLASS_MAPPINGS.items():
        schema = node_class.INPUT_TYPES()
        actual_advanced = set()
        for input_group in ("required", "optional"):
            for input_name, input_spec in schema.get(input_group, {}).items():
                options = _input_options(input_spec)
                display_name = options.get("display_name")
                assert isinstance(display_name, str) and display_name.strip(), (
                    f"{node_id}.{input_group}.{input_name} has no display_name"
                )
                if options.get("advanced") is True:
                    actual_advanced.add(input_name)

        assert actual_advanced == presentation.NODE_ADVANCED_INPUTS[node_id]

    assert {
        name: presentation.INPUT_DISPLAY_NAMES[name]
        for name in (
            "binary_path",
            "keep_context",
            "enable_chaining",
            "image_amount",
            "image_10",
            "structured_output",
        )
    } == {
        "binary_path": "llama-server Binary",
        "keep_context": "Reuse Prompt Prefix Cache",
        "enable_chaining": "Legacy Chaining (Compatibility)",
        "image_amount": "Image Inputs",
        "image_10": "Image 10",
        "structured_output": "Structured Output",
    }
    token_count = node_package.NODE_CLASS_MAPPINGS["LlamaCppTokenCount"].INPUT_TYPES()
    assert _input_options(token_count["required"]["text"])["display_name"] == "Text to Tokenize"


@pytest.mark.parametrize(
    ("node_id", "visible_inputs"),
    (
        (
            "StartLlamaCppServer",
            {
                "model",
                "context_size",
                "binary_path",
                "mmproj",
                "unload_comfy_models_before_start",
            },
        ),
        (
            "StartLlamaCppRouter",
            {
                "context_size",
                "models_max",
                "binary_path",
                "unload_comfy_models_before_start",
                "models_directory",
            },
        ),
        (
            "LlamaCppBasicPrompt",
            {
                "prompt",
                "model",
                "system_prompt",
                "enable_thinking",
                "max_tokens",
                "temperature",
            },
        ),
        (
            "LlamaCppAdvPrompt",
            {
                "prompt",
                "image_amount",
                "model",
                "system_prompt",
                "enable_thinking",
                "max_tokens",
                "temperature",
            },
        ),
        (
            "LlamaCppAdvPPPrompt",
            {
                "template",
                "prompt",
                "image_amount",
                "model",
                "system_prompt",
                "enable_thinking",
                "max_tokens",
                "temperature",
            },
        ),
        (
            "LlamaCppGenerate",
            {
                "prompt",
                "model",
                "system_prompt",
                "thinking_mode",
                "max_tokens",
                "sampling_mode",
                "temperature",
                "top_p",
                "top_k",
                "min_p",
                "repeat_penalty",
                "presence_penalty",
                "frequency_penalty",
                "seed",
                "cache_prompt",
                "image_amount",
            },
        ),
    ),
)
def test_primary_workflow_inputs_remain_visible(node_package, node_id, visible_inputs):
    schema = node_package.NODE_CLASS_MAPPINGS[node_id].INPUT_TYPES()
    inputs = {
        input_name: input_spec
        for input_group in ("required", "optional")
        for input_name, input_spec in schema.get(input_group, {}).items()
    }

    for input_name in visible_inputs:
        assert _input_options(inputs[input_name]).get("advanced") is not True


@pytest.mark.parametrize("input_group", ("required", "optional", "hidden"))
def test_every_registered_input_has_a_tooltip(node_package, input_group):
    missing = []
    for node_id, node_class in node_package.NODE_CLASS_MAPPINGS.items():
        schema = node_class.INPUT_TYPES()
        for input_name, input_spec in schema.get(input_group, {}).items():
            if input_group == "hidden" and isinstance(input_spec, str):
                continue
            tooltip = _input_options(input_spec).get("tooltip")
            if not isinstance(tooltip, str) or not tooltip.strip():
                missing.append(f"{node_id}.{input_group}.{input_name}")

    assert missing == [], f"Inputs missing tooltips: {missing}"


def test_canonical_utility_metadata_names_generate_consumers(node_package):
    for node_id in ("LlamaCppStructuredOutput", "LlamaCppTokenBan"):
        node_class = node_package.NODE_CLASS_MAPPINGS[node_id]
        assert "Generate" in node_class.DESCRIPTION or "Generate" in " ".join(
            node_class.OUTPUT_TOOLTIPS
        )
        assert "Generate" in " ".join(node_class.OUTPUT_TOOLTIPS)
