"""Generation value objects and user-input parsers."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal


def parse_text_list(value: str | Sequence[str] | None) -> list[str]:
    """Parse JSON arrays, newline lists, or the legacy comma-separated form.

    JSON is the unambiguous form for entries containing commas or intentional
    leading/trailing whitespace. Newlines are preferred for ordinary UI input.
    """

    if value is None:
        return []
    if not isinstance(value, str):
        return [str(item) for item in value if str(item)]

    text = value.strip()
    if not text:
        return []

    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON list: {exc}") from exc
        if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
            raise ValueError("JSON list entries must all be strings")
        return [item for item in parsed if item]

    separator = "\n" if "\n" in text else ","
    return [item.strip() for item in text.split(separator) if item.strip()]


@dataclass(frozen=True, slots=True)
class StructuredConstraint:
    """A validated llama-server structured-output constraint."""

    kind: Literal["json_schema", "json_object", "grammar"]
    value: Mapping[str, Any] | str | None = None
    name: str = "comfyui_output"
    strict: bool = True

    def apply(self, payload: dict[str, Any]) -> None:
        if self.kind == "grammar":
            grammar = str(self.value or "").strip()
            if not grammar:
                raise ValueError("Grammar cannot be empty")
            payload["grammar"] = grammar
            payload.pop("response_format", None)
            return

        if self.kind == "json_object":
            response_format: dict[str, Any] = {"type": "json_object"}
            if self.value:
                response_format["schema"] = dict(self.value)
            payload["response_format"] = response_format
            payload.pop("grammar", None)
            return

        if not isinstance(self.value, Mapping) or not self.value:
            raise ValueError("JSON schema must be a non-empty object")
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": self.name,
                "strict": self.strict,
                "schema": dict(self.value),
            },
        }
        payload.pop("grammar", None)


@dataclass(frozen=True, slots=True)
class GenerationOptions:
    """Workflow-visible llama-server chat completion options."""

    max_tokens: int = 2048
    temperature: float = 0.7
    top_p: float = 0.9
    top_k: int = 40
    min_p: float = 0.05
    repeat_penalty: float = 1.1
    presence_penalty: float = 0.0
    frequency_penalty: float = 0.0
    seed: int = 0
    cache_prompt: bool = False
    enable_thinking: bool = True
    stop: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        if self.max_tokens < 1:
            raise ValueError("max_tokens must be at least 1")
        payload: dict[str, Any] = {
            "stream": True,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "top_k": self.top_k,
            "min_p": self.min_p,
            "repeat_penalty": self.repeat_penalty,
            "presence_penalty": self.presence_penalty,
            "frequency_penalty": self.frequency_penalty,
            "seed": self.seed,
            "cache_prompt": self.cache_prompt,
            "chat_template_kwargs": {"enable_thinking": self.enable_thinking},
        }
        if self.stop:
            payload["stop"] = list(self.stop)
        return payload
