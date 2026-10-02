"""Explicit complete-request budgeting through Generate's preparation path."""

from __future__ import annotations

import json
from typing import Any

from ..generation.budget import RequestBudget
from ..generation.execution import CanonicalGenerationExecutor
from .generate import LlamaCppGenerate


class LlamaCppRequestBudget(LlamaCppGenerate):
    DESCRIPTION = (
        "Count the complete chat request without generating text. "
        "Reports input tokens, effective slot context and requested output allowance. "
        "Requires a running target and can perform real model and media work when queued."
    )
    CATEGORY = LlamaCppGenerate.CATEGORY
    SEARCH_ALIASES = ["request budget", "context budget", "input tokens", "context window"]
    RETURN_TYPES = ("STRING", "STRING", "LLAMACPP_REQUEST_BUDGET")
    RETURN_NAMES = ("summary", "budget_json", "budget")
    OUTPUT_TOOLTIPS = (
        "Fit, overflow or unknown for input tokens plus the requested output allowance.",
        "Versioned budget with payload hash, runtime epoch, timing and release evidence.",
        "Typed budget observation. A later Generate node checks its own request again.",
    )

    @classmethod
    def INPUT_TYPES(cls):
        schema = super().INPUT_TYPES()
        options = schema["optional"]
        options["budget_policy"] = (
            ["report", "enforce"],
            {
                "default": "report",
                "display_name": "Budget Policy",
                "tooltip": (
                    "Report returns fit, overflow or unknown. Enforce fails unless input "
                    "plus requested output fits a known effective context. Counting always runs."
                ),
            },
        )
        options["release_after_generation"][1].update(
            display_name="Release After Count",
            tooltip=(
                "Wait for terminal managed runtime/model release after counting. "
                "Unavailable for attached endpoints."
            ),
        )
        options["partial_output_policy"][1].update(
            tooltip="Counts never return partial results; this setting has no effect on counting.",
            advanced=True,
        )
        options["max_tokens"][1].update(
            tooltip="Requested output allowance reserved for budgeting. This node generates no text."
        )
        return schema

    def _execute_generation(self, prompt: str, **options: Any) -> dict[str, Any]:
        envelope = CanonicalGenerationExecutor().budget(prompt, **options)
        budget = RequestBudget.from_dict(envelope["budget"])
        count = "unknown" if budget.input_tokens is None else str(budget.input_tokens)
        context = "unknown" if budget.context_limit is None else str(budget.context_limit)
        remaining = "unknown" if budget.remaining is None else str(budget.remaining)
        summary = (
            f"{budget.status.value.upper()}: input {count}, requested output {budget.max_tokens}, "
            f"context {context}, remaining {remaining}."
        )
        serialized = json.dumps(
            envelope, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )
        return {"ui": {"text": (summary,)}, "result": (summary, serialized, budget)}


NODE_CLASS_MAPPINGS = {"LlamaCppRequestBudget": LlamaCppRequestBudget}
NODE_DISPLAY_NAME_MAPPINGS = {"LlamaCppRequestBudget": "llama.cpp Request Budget"}
