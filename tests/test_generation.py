from __future__ import annotations

import json

import numpy as np
import pytest

from generation.images import image_tensor_to_data_url, image_tensor_to_data_urls
from generation.payloads import build_chat_payload, normalize_structured_output
from generation.templates import apply_template, load_templates
from generation.types import GenerationOptions, StructuredConstraint, parse_text_list


def test_parse_text_list_supports_json_newlines_and_legacy_commas():
    assert parse_text_list('["a,b", " c "]') == ["a,b", " c "]
    assert parse_text_list("one\ntwo") == ["one", "two"]
    assert parse_text_list("one, two") == ["one", "two"]


def test_parse_text_list_rejects_non_string_json_entries():
    with pytest.raises(ValueError, match="must all be strings"):
        parse_text_list('["ok", 2]')


def test_generation_options_preserve_legacy_defaults():
    payload = GenerationOptions().to_payload()
    assert payload["max_tokens"] == 2048
    assert payload["temperature"] == 0.7
    assert payload["repeat_penalty"] == 1.1
    assert payload["cache_prompt"] is False
    assert payload["chat_template_kwargs"] == {"enable_thinking": True}


def test_multimodal_payload_and_nested_json_schema():
    payload = build_chat_payload(
        "describe",
        system_prompt="be exact",
        images=["data:image/png;base64,abc"],
        options=GenerationOptions(stop=("END",)),
        model="vision",
        structured_output=StructuredConstraint("json_schema", {"type": "object", "properties": {}}),
    )
    assert payload["messages"][0] == {"role": "system", "content": "be exact"}
    assert payload["messages"][1]["content"][0]["type"] == "image_url"
    assert payload["messages"][1]["content"][-1] == {"type": "text", "text": "describe"}
    assert payload["model"] == "vision"
    assert payload["stop"] == ["END"]
    schema = payload["response_format"]["json_schema"]
    assert schema["schema"]["type"] == "object"
    assert schema["strict"] is True


def test_legacy_structured_output_shapes_normalize():
    constraint = normalize_structured_output(
        {"type": "json_schema", "json_schema": {"type": "array"}}
    )
    assert constraint == StructuredConstraint("json_schema", {"type": "array"})
    nested = normalize_structured_output(
        {
            "type": "json_schema",
            "json_schema": {"name": "x", "strict": False, "schema": {"type": "string"}},
        }
    )
    assert nested == StructuredConstraint("json_schema", {"type": "string"}, "x", False)


def test_grammar_and_schema_are_mutually_exclusive():
    payload = {"response_format": {"type": "json_object"}}
    StructuredConstraint("grammar", 'root ::= "ok"').apply(payload)
    assert "response_format" not in payload
    assert payload["grammar"] == 'root ::= "ok"'


def test_image_conversion_preserves_legacy_first_batch_policy():
    batch = np.zeros((2, 2, 3, 3), dtype=np.float32)
    batch[1] = 1.0
    first = image_tensor_to_data_url(batch)
    all_images = image_tensor_to_data_urls(batch, include_batch=True)
    assert first.startswith("data:image/png;base64,")
    assert len(all_images) == 2
    assert first == all_images[0]
    assert all_images[0] != all_images[1]


def test_image_conversion_rejects_invalid_channels():
    with pytest.raises(ValueError, match="channels"):
        image_tensor_to_data_url(np.zeros((2, 2, 2), dtype=np.float32))


def test_template_backend_fills_only_blank_fields(tmp_path):
    path = tmp_path / "templates.json"
    path.write_text(
        json.dumps({"Example": {"system_prompt": "system", "prompt": "prompt"}}),
        encoding="utf-8",
    )
    assert "Empty" in load_templates(path)
    assert apply_template("Example", "", "", path=path) == ("prompt", "system")
    assert apply_template("Example", "custom", "mine", path=path) == ("custom", "mine")
