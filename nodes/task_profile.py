"""Workflow-portable task profile snapshot node."""

from __future__ import annotations

from ..generation.profiles import FREEFORM_SNAPSHOT_JSON, parse_profile_snapshot
from .presentation import NODE_CATEGORIES, NODE_SEARCH_ALIASES


class LlamaCppTaskProfile:
    DESCRIPTION = "Stores one portable task-profile snapshot for canonical llama.cpp generation."
    CATEGORY = NODE_CATEGORIES["LlamaCppTaskProfile"]
    SEARCH_ALIASES = NODE_SEARCH_ALIASES["LlamaCppTaskProfile"]
    RETURN_TYPES = ("LLAMACPP_PROFILE",)
    RETURN_NAMES = ("profile",)
    OUTPUT_TOOLTIPS = (
        "Validated saved profile snapshot; execution never reloads a mutable local name.",
    )
    FUNCTION = "create_profile"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "profile_snapshot": (
                    "STRING",
                    {
                        "default": FREEFORM_SNAPSHOT_JSON,
                        "multiline": True,
                        "hidden": True,
                        "display_name": "Saved Profile Snapshot",
                        "tooltip": (
                            "Portable compact JSON copied explicitly from a local profile. "
                            "The saved snapshot is authoritative during execution."
                        ),
                    },
                )
            }
        }

    def create_profile(self, profile_snapshot: str = FREEFORM_SNAPSHOT_JSON):
        return (parse_profile_snapshot(profile_snapshot),)


NODE_CLASS_MAPPINGS = {"LlamaCppTaskProfile": LlamaCppTaskProfile}
NODE_DISPLAY_NAME_MAPPINGS = {"LlamaCppTaskProfile": "llama.cpp Task Profile"}

__all__ = [
    "LlamaCppTaskProfile",
    "NODE_CLASS_MAPPINGS",
    "NODE_DISPLAY_NAME_MAPPINGS",
]
