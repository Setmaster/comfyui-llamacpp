"""Compatibility characterization for every node released in v0.2.1.

These tests intentionally lock public ComfyUI and Python import contracts while
allowing implementation internals and additional nodes/inputs to evolve.
"""

from __future__ import annotations

import importlib
import inspect
import runpy
from pathlib import Path

import pytest

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "workflows"
RELEASED_NODE_IDS = (
    "StartLlamaCppServer",
    "StopLlamaCppServer",
    "LlamaCppServerStatus",
    "LlamaCppBasicPrompt",
    "StartLlamaCppRouter",
    "LlamaCppListModels",
    "LlamaCppLoadModel",
    "LlamaCppUnloadModel",
    "LlamaCppPromptOutput",
    "LlamaCppAdvPrompt",
    "LlamaCppAdvPPPrompt",
    "LlamaCppTokenBan",
)

PRIMITIVE_TYPES = {"BOOLEAN", "FLOAT", "INT", "STRING"}


def _schema(node_class) -> dict:
    schema = node_class.INPUT_TYPES()
    assert isinstance(schema, dict)
    assert isinstance(schema.get("required", {}), dict)
    assert isinstance(schema.get("optional", {}), dict)
    return schema


def _find_input(schema: dict, name: str):
    for category in ("required", "optional", "hidden"):
        inputs = schema.get(category, {})
        if name in inputs:
            return category, inputs[name]
    raise AssertionError(f"Input {name!r} is missing from INPUT_TYPES")


def _type_declaration(input_spec):
    assert isinstance(input_spec, (tuple, list)) and input_spec
    return input_spec[0]


def _options(input_spec) -> dict:
    if len(input_spec) < 2 or not isinstance(input_spec[1], dict):
        return {}
    return input_spec[1]


def _is_primitive_widget(input_spec) -> bool:
    options = _options(input_spec)
    if options.get("forceInput"):
        return False

    input_type = _type_declaration(input_spec)
    if isinstance(input_type, (tuple, list)):
        return True
    return input_type in PRIMITIVE_TYPES


def _primitive_widget_names(schema: dict) -> list[str]:
    names = []
    for category in ("required", "optional"):
        for name, input_spec in schema.get(category, {}).items():
            if _is_primitive_widget(input_spec):
                names.append(name)
    return names


def _assert_prefix(actual: list[str], expected: list[str], description: str) -> None:
    assert actual[: len(expected)] == expected, (
        f"{description} changed. Historical values are positional.\n"
        f"Expected prefix: {expected}\n"
        f"Actual order:    {actual}"
    )


@pytest.mark.parametrize("node_id", RELEASED_NODE_IDS)
def test_released_node_identity_and_outputs(node_package, released_contracts, node_id):
    contract = released_contracts["nodes"][node_id]

    assert node_id in node_package.NODE_CLASS_MAPPINGS
    assert node_id in node_package.NODE_DISPLAY_NAME_MAPPINGS

    node_class = node_package.NODE_CLASS_MAPPINGS[node_id]
    module = importlib.import_module(f"{node_package.__name__}.{contract['module']}")

    assert node_class is getattr(module, contract["class_name"])
    assert node_class.__name__ == contract["class_name"]
    assert node_package.NODE_DISPLAY_NAME_MAPPINGS[node_id] == contract["display_name"]
    # Category is presentation metadata, not a saved-workflow identity contract.
    assert node_class.FUNCTION == contract["function"]
    assert callable(getattr(node_class, contract["function"]))
    assert tuple(node_class.RETURN_TYPES) == tuple(contract["return_types"])
    assert tuple(node_class.RETURN_NAMES) == tuple(contract["return_names"])
    assert bool(getattr(node_class, "OUTPUT_NODE", False)) is contract["output_node"]


def test_all_released_node_ids_remain_registered(node_package, released_contracts):
    expected = set(released_contracts["nodes"])
    assert expected == set(RELEASED_NODE_IDS)
    assert expected <= set(node_package.NODE_CLASS_MAPPINGS)
    assert expected <= set(node_package.NODE_DISPLAY_NAME_MAPPINGS)


@pytest.mark.parametrize("node_id", RELEASED_NODE_IDS)
def test_legacy_input_and_widget_order_is_a_prefix(node_package, released_contracts, node_id):
    contract = released_contracts["nodes"][node_id]
    node_class = node_package.NODE_CLASS_MAPPINGS[node_id]
    schema = _schema(node_class)

    _assert_prefix(
        list(schema.get("required", {})),
        contract["required_order"],
        f"{node_id} required input order",
    )
    _assert_prefix(
        list(schema.get("optional", {})),
        contract["optional_order"],
        f"{node_id} optional input order",
    )
    _assert_prefix(
        _primitive_widget_names(schema),
        contract["primitive_widget_prefix"],
        f"{node_id} primitive widget order",
    )


@pytest.mark.parametrize("node_id", RELEASED_NODE_IDS)
def test_legacy_python_call_signature_is_a_prefix(node_package, released_contracts, node_id):
    contract = released_contracts["nodes"][node_id]
    node_class = node_package.NODE_CLASS_MAPPINGS[node_id]
    method = getattr(node_class, contract["function"])

    parameters = []
    for parameter in inspect.signature(method).parameters.values():
        if parameter.name == "self":
            continue
        if parameter.kind in (
            inspect.Parameter.VAR_KEYWORD,
            inspect.Parameter.VAR_POSITIONAL,
        ):
            continue
        parameters.append(parameter.name)

    expected = contract["required_order"] + contract["optional_order"]
    _assert_prefix(parameters, expected, f"{node_id} Python call signature")


@pytest.mark.parametrize("node_id", RELEASED_NODE_IDS)
def test_released_widget_defaults_are_preserved(node_package, released_contracts, node_id):
    contract = released_contracts["nodes"][node_id]
    schema = _schema(node_package.NODE_CLASS_MAPPINGS[node_id])

    for input_name, expected in contract["defaults"].items():
        _, input_spec = _find_input(schema, input_name)
        options = _options(input_spec)
        assert "default" in options, f"{node_id}.{input_name} lost its default"

        if isinstance(expected, dict) and expected.get("kind") == "first_choice":
            choices = _type_declaration(input_spec)
            assert isinstance(choices, (tuple, list)) and choices
            assert options["default"] == choices[0]
        else:
            assert options["default"] == expected


@pytest.mark.parametrize("node_id", RELEASED_NODE_IDS)
def test_released_input_socket_types_and_options(node_package, released_contracts, node_id):
    contract = released_contracts["nodes"][node_id]
    schema = _schema(node_package.NODE_CLASS_MAPPINGS[node_id])

    for input_name, expected_type in contract.get("input_sockets", {}).items():
        _, input_spec = _find_input(schema, input_name)
        assert _type_declaration(input_spec) == expected_type

    for input_name, expected_options in contract.get("input_options", {}).items():
        _, input_spec = _find_input(schema, input_name)
        actual_options = _options(input_spec)
        for option_name, expected_value in expected_options.items():
            assert actual_options.get(option_name) == expected_value


@pytest.mark.parametrize("node_id", ("LlamaCppAdvPrompt", "LlamaCppAdvPPPrompt"))
def test_vision_nodes_declare_all_backend_image_inputs(node_package, released_contracts, node_id):
    """Frontend-only sockets are dropped by current Comfy V1 execution."""

    schema = _schema(node_package.NODE_CLASS_MAPPINGS[node_id])
    optional = schema.get("optional", {})
    expected_count = released_contracts["backend_image_sockets"][node_id]

    expected_names = [f"image_{index}" for index in range(1, expected_count + 1)]
    for input_name in expected_names:
        assert input_name in optional, (
            f"{node_id} must declare {input_name} in Python INPUT_TYPES; "
            "a JavaScript-only socket is not forwarded by current Comfy V1"
        )
        assert _type_declaration(optional[input_name]) == "IMAGE"


def test_historical_workflow_covers_every_released_node(historical_workflow, released_contracts):
    workflow_types = [node["type"] for node in historical_workflow["nodes"]]
    assert len(workflow_types) == len(set(workflow_types))
    assert set(workflow_types) == set(released_contracts["nodes"])
    assert historical_workflow["version"] == 0.4
    assert historical_workflow["extra"]["llamacpp_contract_fixture"] == {
        "release": released_contracts["release"],
        "source_commit": released_contracts["source_commit"],
    }


@pytest.mark.parametrize("node_id", RELEASED_NODE_IDS)
def test_historical_widget_values_fit_the_legacy_prefix(
    historical_workflow, released_contracts, node_id
):
    contract = released_contracts["nodes"][node_id]
    workflow_node = next(node for node in historical_workflow["nodes"] if node["type"] == node_id)
    widget_values = workflow_node["widgets_values"]
    expected_widget_count = len(contract["primitive_widget_prefix"])

    if node_id == "LlamaCppPromptOutput":
        # Its frontend appends one UI-only persisted output widget after the
        # backend plaintext widget.  The backend prefix must remain stable.
        assert len(widget_values) == expected_widget_count + 1
    else:
        assert len(widget_values) == expected_widget_count


def test_historical_zero_image_count_is_retained(historical_workflow, released_contracts):
    node = next(
        item for item in historical_workflow["nodes"] if item["type"] == "LlamaCppAdvPrompt"
    )
    widget_order = released_contracts["nodes"]["LlamaCppAdvPrompt"]["primitive_widget_prefix"]
    image_amount_index = widget_order.index("image_amount")
    assert node["widgets_values"][image_amount_index] == 0


def test_root_and_compatibility_module_imports(node_package, released_contracts):
    for export_name in released_contracts["root_exports"]:
        assert hasattr(node_package, export_name)

    assert isinstance(node_package.__version__, str) and node_package.__version__
    assert node_package.WEB_DIRECTORY == "./web"

    for module_name, export_names in released_contracts["compatibility_modules"].items():
        module = importlib.import_module(f"{node_package.__name__}.{module_name}")
        for export_name in export_names:
            assert hasattr(module, export_name), f"{module_name}.{export_name} missing"


def test_direct_tooling_import_uses_the_single_source_package_version(capsys):
    root = FIXTURE_ROOT.parents[2]
    direct = runpy.run_path(str(root / "__init__.py"))
    version = runpy.run_path(str(root / "_version.py"))

    assert direct["__version__"] == version["__version__"]
    capsys.readouterr()


def test_contract_fixture_is_anchored_to_the_released_commit(released_contracts):
    assert released_contracts["release"] == "0.2.1"
    assert released_contracts["source_commit"] == ("1e3b7a5a2d90ee3a40cf40e01784de1c343ea85a")
    assert FIXTURE_ROOT.joinpath("v0_2_1_saved_workflow.json").is_file()
