"""Workflow node for llama-server text-form logit bias entries."""

from ..generation.types import parse_text_list
from .presentation import NODE_CATEGORIES, NODE_SEARCH_ALIASES, apply_input_presentation


class LlamaCppTokenBan:
    DESCRIPTION = (
        "Builds a llama-server token-ban list from newline, JSON-array, or legacy "
        "comma-separated text entries."
    )
    CATEGORY = NODE_CATEGORIES["LlamaCppTokenBan"]
    SEARCH_ALIASES = NODE_SEARCH_ALIASES["LlamaCppTokenBan"]
    RETURN_TYPES = ("LOGIT_BIAS",)
    RETURN_NAMES = ("logit_bias",)
    OUTPUT_TOOLTIPS = ("Token-ban entries for an ADV++ Prompt node.",)
    FUNCTION = "create_ban_list"

    @classmethod
    def INPUT_TYPES(cls):
        schema = {
            "required": {
                "banned_tokens": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": "",
                        "placeholder": "One entry per line, JSON list, or legacy comma list",
                        "tooltip": (
                            "Text or token strings to ban. Use a JSON array when an entry "
                            "contains commas or meaningful surrounding whitespace."
                        ),
                    },
                ),
                "enable": (
                    "BOOLEAN",
                    {"default": True, "tooltip": "Enable or disable token banning."},
                ),
            }
        }
        return apply_input_presentation("LlamaCppTokenBan", schema)

    def create_ban_list(self, banned_tokens: str, enable: bool):
        if not enable or not banned_tokens.strip():
            return (None,)
        tokens = parse_text_list(banned_tokens)
        if not tokens:
            return (None,)
        return ([[token, False] for token in tokens],)


NODE_CLASS_MAPPINGS = {"LlamaCppTokenBan": LlamaCppTokenBan}
NODE_DISPLAY_NAME_MAPPINGS = {"LlamaCppTokenBan": "llama.cpp Token Ban"}
