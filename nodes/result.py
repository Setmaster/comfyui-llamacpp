"""Expose the existing generation result contract to ordinary text nodes."""

from __future__ import annotations

from ..generation.contracts import GenerationResult
from .presentation import NODE_CATEGORIES, NODE_SEARCH_ALIASES, apply_input_presentation


class LlamaCppResult:
    DESCRIPTION = (
        "Serializes a Generate result as JSON, including full response and thinking text, "
        "completion state, usage, warnings, and release evidence. Connect to Prompt Output "
        "to inspect it. Partial and cancelled results retain their original state."
    )
    CATEGORY = NODE_CATEGORIES["LlamaCppResult"]
    SEARCH_ALIASES = NODE_SEARCH_ALIASES["LlamaCppResult"]
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("result_json",)
    OUTPUT_TOOLTIPS = ("The complete versioned GenerationResult JSON, without changing its state.",)
    FUNCTION = "serialize"

    @classmethod
    def INPUT_TYPES(cls):
        return apply_input_presentation(
            "LlamaCppResult",
            {
                "required": {
                    "result": (
                        "LLAMACPP_GENERATION_RESULT",
                        {"tooltip": "Typed result from llama.cpp Generate."},
                    ),
                },
            },
            display_names={"result": "Generation Result"},
        )

    def serialize(self, result: GenerationResult):
        if not isinstance(result, GenerationResult):
            raise TypeError("Expected a typed generation result from llama.cpp Generate.")
        return (result.to_json(),)


NODE_CLASS_MAPPINGS = {"LlamaCppResult": LlamaCppResult}
NODE_DISPLAY_NAME_MAPPINGS = {"LlamaCppResult": "llama.cpp Result JSON"}
