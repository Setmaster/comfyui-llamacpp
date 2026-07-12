"""Immutable characterization of the complete released 0.3 node surface."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "workflows" / "v0_3_0_contracts.json"
PRIMITIVE_TYPES = {"BOOLEAN", "FLOAT", "INT", "STRING"}
DYNAMIC_CHOICES = {"model", "model_name", "mmproj", "models_directory"}


@pytest.fixture(scope="module")
def v0_3_contracts() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _options(input_spec) -> dict:
    if len(input_spec) > 1 and isinstance(input_spec[1], dict):
        return input_spec[1]
    return {}


def _is_widget(input_spec) -> bool:
    options = _options(input_spec)
    declared = input_spec[0]
    return not options.get("forceInput") and (
        isinstance(declared, (list, tuple)) or declared in PRIMITIVE_TYPES
    )


def _declared_type(name: str, input_spec):
    declared = input_spec[0]
    if not isinstance(declared, (list, tuple)):
        return declared
    if name in DYNAMIC_CHOICES:
        return {"kind": "dynamic_choices"}
    return {"choices": list(declared)}


def _field_snapshot(schema: dict) -> list[dict]:
    fields = []
    for group in ("required", "optional", "hidden"):
        for name, input_spec in schema.get(group, {}).items():
            options = _options(input_spec)
            field = {
                "name": name,
                "group": group,
                "type": _declared_type(name, input_spec),
                "widget": _is_widget(input_spec),
            }
            if "default" in options:
                default = options["default"]
                choices = input_spec[0]
                field["default"] = (
                    {"kind": "first_choice"}
                    if name in DYNAMIC_CHOICES
                    and isinstance(choices, (list, tuple))
                    and choices
                    and default == choices[0]
                    else default
                )
            if "forceInput" in options:
                field["forceInput"] = options["forceInput"]
            fields.append(field)
    return fields


def _call_parameters(node_class) -> list[str]:
    method = getattr(node_class, node_class.FUNCTION)
    return [
        parameter.name
        for parameter in inspect.signature(method).parameters.values()
        if parameter.name != "self"
        and parameter.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    ]


def _assert_prefix(actual: list, expected: list, label: str) -> None:
    assert actual[: len(expected)] == expected, (
        f"Released 0.3 {label} changed. Additive fields must follow the complete "
        f"0.3 sequence.\nExpected prefix: {expected}\nActual: {actual}"
    )


def test_v0_3_fixture_is_anchored_to_immutable_release(v0_3_contracts):
    assert v0_3_contracts["release"] == "0.3.0"
    assert v0_3_contracts["source_commit"] == ("365986af4a47426b5513b3cee917ebec93a4204a")
    assert len(v0_3_contracts["nodes"]) == 17


def test_complete_v0_3_node_surface_remains_registered(node_package, v0_3_contracts):
    expected_ids = set(v0_3_contracts["nodes"])
    assert expected_ids <= set(node_package.NODE_CLASS_MAPPINGS)
    assert expected_ids <= set(node_package.NODE_DISPLAY_NAME_MAPPINGS)


@pytest.mark.parametrize("node_id", json.loads(FIXTURE.read_text())["nodes"])
def test_complete_v0_3_contract_is_an_exact_compatibility_prefix(
    node_package, v0_3_contracts, node_id
):
    expected = v0_3_contracts["nodes"][node_id]
    node_class = node_package.NODE_CLASS_MAPPINGS[node_id]
    schema = node_class.INPUT_TYPES()
    fields = _field_snapshot(schema)

    assert node_class.__name__ == expected["class_name"]
    assert node_package.NODE_DISPLAY_NAME_MAPPINGS[node_id] == expected["display_name"]
    assert node_class.FUNCTION == expected["function"]
    assert list(node_class.RETURN_TYPES) == expected["return_types"]
    assert list(node_class.RETURN_NAMES) == expected["return_names"]
    assert bool(getattr(node_class, "OUTPUT_NODE", False)) is expected["output_node"]

    _assert_prefix(_call_parameters(node_class), expected["parameters"], f"{node_id} call order")
    _assert_prefix(fields, expected["inputs"], f"{node_id} input schema")
    _assert_prefix(
        [field["name"] for field in fields if field["widget"]],
        expected["primitive_widget_order"],
        f"{node_id} primitive widget order",
    )


def test_v0_3_frontend_seed_companion_position_is_preserved_in_examples(node_package):
    """Current Comfy serializes one auxiliary control immediately after seed."""

    root = Path(__file__).resolve().parents[1] / "example_workflows"
    prompt_types = {
        "LlamaCppBasicPrompt",
        "LlamaCppAdvPrompt",
        "LlamaCppAdvPPPrompt",
    }
    observed = set()

    for path in root.glob("*.json"):
        workflow = json.loads(path.read_text(encoding="utf-8"))
        for node in workflow["nodes"]:
            if node["type"] not in prompt_types:
                continue
            observed.add(node["type"])
            schema = node_package.NODE_CLASS_MAPPINGS[node["type"]].INPUT_TYPES()
            widget_names = [field["name"] for field in _field_snapshot(schema) if field["widget"]]
            seed_index = widget_names.index("seed")
            assert node["widgets_values"][seed_index + 1] in {
                "fixed",
                "increment",
                "decrement",
                "randomize",
            }
            assert len(node["widgets_values"]) == len(widget_names) + 1

    assert {"LlamaCppBasicPrompt", "LlamaCppAdvPPPrompt"} <= observed
