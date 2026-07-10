"""User-facing metadata checks for the complete registered node surface."""

from __future__ import annotations

import pytest


def _input_options(input_spec) -> dict:
    if len(input_spec) > 1 and isinstance(input_spec[1], dict):
        return input_spec[1]
    return {}


def test_every_registered_node_has_a_concise_description(node_package):
    assert len(node_package.NODE_CLASS_MAPPINGS) == 17

    for node_id, node_class in node_package.NODE_CLASS_MAPPINGS.items():
        description = getattr(node_class, "DESCRIPTION", None)
        assert isinstance(description, str), f"{node_id} has no DESCRIPTION"
        assert description == description.strip(), f"{node_id} DESCRIPTION has outer whitespace"
        assert 20 <= len(description) <= 240, f"{node_id} DESCRIPTION is not concise"


def test_every_registered_output_has_a_tooltip(node_package):
    for node_id, node_class in node_package.NODE_CLASS_MAPPINGS.items():
        output_tooltips = getattr(node_class, "OUTPUT_TOOLTIPS", None)
        assert isinstance(output_tooltips, tuple), f"{node_id} has no OUTPUT_TOOLTIPS"
        assert len(output_tooltips) == len(node_class.RETURN_TYPES), (
            f"{node_id} output tooltip count does not match RETURN_TYPES"
        )
        assert all(isinstance(value, str) and value.strip() for value in output_tooltips), (
            f"{node_id} has an empty output tooltip"
        )


@pytest.mark.parametrize("input_group", ("required", "optional", "hidden"))
def test_every_registered_input_has_a_tooltip(node_package, input_group):
    missing = []
    for node_id, node_class in node_package.NODE_CLASS_MAPPINGS.items():
        schema = node_class.INPUT_TYPES()
        for input_name, input_spec in schema.get(input_group, {}).items():
            tooltip = _input_options(input_spec).get("tooltip")
            if not isinstance(tooltip, str) or not tooltip.strip():
                missing.append(f"{node_id}.{input_group}.{input_name}")

    assert missing == [], f"Inputs missing tooltips: {missing}"
