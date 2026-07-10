"""OpenAI-compatible llama-server chat payload construction."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .types import GenerationOptions, StructuredConstraint


def normalize_structured_output(value: Any) -> StructuredConstraint | None:
    """Accept typed constraints plus the node pack's legacy dictionary shape."""

    if value is None or isinstance(value, StructuredConstraint):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("Structured output must be a constraint object")

    kind = value.get("type")
    if kind == "grammar":
        return StructuredConstraint("grammar", value.get("grammar", ""))
    if kind == "json_object":
        return StructuredConstraint("json_object", value.get("schema"))
    if kind == "json_schema":
        schema = value.get("json_schema")
        if isinstance(schema, Mapping) and "schema" in schema:
            nested = schema
            return StructuredConstraint(
                "json_schema",
                nested.get("schema"),
                name=str(nested.get("name") or "comfyui_output"),
                strict=bool(nested.get("strict", True)),
            )
        return StructuredConstraint("json_schema", schema)
    raise ValueError(f"Unsupported structured output type: {kind!r}")


def build_chat_payload(
    prompt: str,
    *,
    system_prompt: str = "",
    images: Sequence[str] = (),
    options: GenerationOptions | None = None,
    model: str | None = None,
    logit_bias: Any = None,
    structured_output: Any = None,
) -> dict[str, Any]:
    """Build a single request payload for text or multimodal chat."""

    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("Prompt cannot be empty")

    user_content: str | list[dict[str, Any]]
    if images:
        user_content = [
            {"type": "image_url", "image_url": {"url": image_url}} for image_url in images
        ]
        user_content.append({"type": "text", "text": prompt})
    else:
        user_content = prompt

    messages: list[dict[str, Any]] = []
    if system_prompt.strip():
        messages.append({"role": "system", "content": system_prompt.strip()})
    messages.append({"role": "user", "content": user_content})

    payload = (options or GenerationOptions()).to_payload()
    payload["messages"] = messages
    if model and model.strip() and model != "(use running model)":
        payload["model"] = model.strip()
    if logit_bias:
        payload["logit_bias"] = logit_bias

    constraint = normalize_structured_output(structured_output)
    if constraint:
        constraint.apply(payload)
    return payload
