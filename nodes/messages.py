"""Small graph-owned conversation builders and portable snapshot import."""

from __future__ import annotations

from ..generation.messages import MESSAGE_ROLES, ConversationMessages
from .presentation import NODE_CATEGORIES, NODE_SEARCH_ALIASES, apply_input_presentation


class LlamaCppMessage:
    DESCRIPTION = (
        "Append one exact text turn to explicit history. Generate adds its current prompt "
        "after this history. No hidden conversation state is retained."
    )
    CATEGORY = NODE_CATEGORIES["LlamaCppMessage"]
    SEARCH_ALIASES = NODE_SEARCH_ALIASES["LlamaCppMessage"]
    RETURN_TYPES = ("LLAMACPP_MESSAGES", "STRING")
    RETURN_NAMES = ("messages", "messages_json")
    OUTPUT_TOOLTIPS = ("Immutable text history for Generate.", "Versioned portable JSON history.")
    FUNCTION = "append"

    @classmethod
    def INPUT_TYPES(cls):
        return apply_input_presentation(
            "LlamaCppMessage",
            {
                "required": {
                    "role": (list(MESSAGE_ROLES), {"default": "user", "tooltip": "Turn role."}),
                    "content": (
                        "STRING",
                        {"multiline": True, "tooltip": "Exact text of this turn."},
                    ),
                },
                "optional": {
                    "messages": ("LLAMACPP_MESSAGES", {"tooltip": "Earlier explicit text turns."})
                },
            },
        )

    def append(self, role: str, content: str, messages: ConversationMessages | None = None):
        if messages is not None and not isinstance(messages, ConversationMessages):
            raise TypeError("messages must come from a llama.cpp messages node")
        history = (messages or ConversationMessages()).append(role, content)
        return history, history.to_json()


class LlamaCppMessages:
    DESCRIPTION = (
        "Import a bounded versioned text-history snapshot. Roles and whitespace are preserved."
    )
    CATEGORY = NODE_CATEGORIES["LlamaCppMessages"]
    SEARCH_ALIASES = NODE_SEARCH_ALIASES["LlamaCppMessages"]
    RETURN_TYPES = ("LLAMACPP_MESSAGES",)
    RETURN_NAMES = ("messages",)
    OUTPUT_TOOLTIPS = ("Immutable text history for Generate or Append Message.",)
    FUNCTION = "load"

    @classmethod
    def INPUT_TYPES(cls):
        return apply_input_presentation(
            "LlamaCppMessages",
            {
                "required": {
                    "messages_json": (
                        "STRING",
                        {
                            "multiline": True,
                            "default": '{"schema_version":1,"messages":[]}',
                            "tooltip": "Version 1 history JSON from Append Message or an exported graph.",
                        },
                    )
                }
            },
        )

    def load(self, messages_json: str):
        return (ConversationMessages.from_json(messages_json),)


NODE_CLASS_MAPPINGS = {"LlamaCppMessage": LlamaCppMessage, "LlamaCppMessages": LlamaCppMessages}
NODE_DISPLAY_NAME_MAPPINGS = {
    "LlamaCppMessage": "llama.cpp Append Message",
    "LlamaCppMessages": "llama.cpp Messages from JSON",
}
