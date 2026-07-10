"""Structured JSON and grammar constraints for llama-server generation."""

from __future__ import annotations

import json
from collections.abc import Mapping


class LlamaCppStructuredOutput:
    DESCRIPTION = (
        "Builds a JSON Schema, JSON object, or GBNF grammar constraint for ADV++ generation."
    )
    CATEGORY = "LlamaCpp"
    RETURN_TYPES = ("STRUCTURED_OUTPUT",)
    RETURN_NAMES = ("structured_output",)
    OUTPUT_TOOLTIPS = ("Validated structured-output constraint for an ADV++ Prompt node.",)
    FUNCTION = "create_constraint"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "mode": (
                    ["json_schema", "json_object", "grammar"],
                    {
                        "default": "json_schema",
                        "tooltip": "Constraint type to send to llama-server.",
                    },
                ),
                "constraint": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": "",
                        "placeholder": (
                            '{"type":"object","properties":{"name":{"type":"string"}},'
                            '"required":["name"]}'
                        ),
                        "tooltip": "JSON schema object, or GBNF grammar for grammar mode.",
                    },
                ),
                "enable": (
                    "BOOLEAN",
                    {"default": True, "tooltip": "Enable or bypass this constraint."},
                ),
            },
            "optional": {
                "schema_name": (
                    "STRING",
                    {"default": "comfyui_output", "tooltip": "OpenAI JSON schema name."},
                ),
                "strict": (
                    "BOOLEAN",
                    {"default": True, "tooltip": "Request strict JSON schema adherence."},
                ),
            },
        }

    def create_constraint(
        self,
        mode: str,
        constraint: str,
        enable: bool,
        schema_name: str = "comfyui_output",
        strict: bool = True,
    ):
        if not enable:
            return (None,)

        text = constraint.strip()
        if mode == "grammar":
            if not text:
                raise ValueError("GBNF grammar cannot be empty")
            return ({"type": "grammar", "grammar": text},)

        if text:
            try:
                schema = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON schema: {exc}") from exc
            if not isinstance(schema, Mapping):
                raise ValueError("JSON schema must be an object")
        elif mode == "json_object":
            schema = None
        else:
            raise ValueError("JSON schema cannot be empty")

        if mode == "json_object":
            result = {"type": "json_object"}
            if schema:
                result["schema"] = dict(schema)
            return (result,)

        return (
            {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name.strip() or "comfyui_output",
                    "strict": strict,
                    "schema": dict(schema),
                },
            },
        )


NODE_CLASS_MAPPINGS = {"LlamaCppStructuredOutput": LlamaCppStructuredOutput}
NODE_DISPLAY_NAME_MAPPINGS = {"LlamaCppStructuredOutput": "llama.cpp Structured Output"}
