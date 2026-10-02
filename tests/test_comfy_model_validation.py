"""Check Generate against the installed Comfy validator without starting Comfy.

Set COMFYUI_ROOT to run these integration cases against another checkout. The
functions are extracted from that checkout unchanged; host services are inert.
The portable validator and execution checks live in test_canonical_generation.
"""

from __future__ import annotations
import __future__

import ast
import asyncio
import importlib
import inspect
import os
import sys
import traceback
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_functions(path, names, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    functions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    ]
    assert {node.name for node in functions} == set(names)
    selected = ast.Module(body=functions, type_ignores=[])
    exec(
        compile(selected, str(path), "exec", flags=__future__.annotations.compiler_flag),
        namespace,
    )


@pytest.fixture
def comfy_validator(node_package, monkeypatch):
    configured = os.environ.get("COMFYUI_ROOT")
    roots = [Path(configured)] if configured else [Path("/mnt/c/ComfyUI"), Path("C:/ComfyUI")]
    root = next((path for path in roots if (path / "execution.py").is_file()), None)
    if root is None:
        if configured:
            pytest.fail("COMFYUI_ROOT does not contain execution.py")
        pytest.skip("Set COMFYUI_ROOT to exercise an installed Comfy validator")
    module = importlib.import_module(f"{node_package.__name__}.nodes.generate")
    monkeypatch.setattr(module, "get_local_models", lambda: ["bundle/local.gguf"])
    node_class = module.LlamaCppGenerate
    mappings = {"LlamaCppGenerate": node_class}
    io = SimpleNamespace(
        AnyType=SimpleNamespace(io_type="*"),
        MatchType=SimpleNamespace(io_type="MATCH"),
        Combo=SimpleNamespace(io_type="COMBO"),
    )
    namespace = {
        "asyncio": asyncio,
        "inspect": inspect,
        "sys": sys,
        "traceback": traceback,
        "nodes": SimpleNamespace(NODE_CLASS_MAPPINGS=mappings),
        "_ComfyNodeInternal": type("InertV3Node", (), {}),
        "ExecutionBlocker": type("InertExecutionBlocker", (), {}),
        "CurrentNodeContext": lambda *args: nullcontext(),
        "is_class": inspect.isclass,
        "io": io,
        "IO": io,
    }
    for relative, names in (
        ("comfy_execution/graph.py", ("get_input_info",)),
        ("comfy_execution/graph_utils.py", ("is_link",)),
        ("comfy_execution/validation.py", ("validate_node_input",)),
        (
            "execution.py",
            (
                "validate_inputs",
                "get_input_data",
                "_async_map_node_over_list",
                "resolve_map_node_over_list_results",
                "full_type_name",
            ),
        ),
    ):
        _load_functions(root / relative, names, namespace)

    def validate(inputs, sources=None):
        prompt = {"generate": {"class_type": "LlamaCppGenerate", "inputs": dict(inputs)}}
        prompt.update(sources or {})
        result = asyncio.run(namespace["validate_inputs"]("prompt-id", prompt, "generate", {}))
        return result, prompt["generate"]["inputs"]

    return validate, mappings, node_class


@pytest.mark.parametrize(
    "model",
    [
        "qwen-vl-4B-Instruct",
        "attached/model:latest",
        "saved/missing-model.gguf",
        "bundle/local.gguf",
        "(use running model)",
        "  exact attached ID  ",
    ],
)
def test_installed_comfy_accepts_exact_model_ids_without_changing_text(comfy_validator, model):
    validate, _, _ = comfy_validator
    (valid, errors, _), inputs = validate({"prompt": "hello", "model": model})
    assert (valid, errors) == (True, [])
    assert inputs["model"] == model


def test_installed_comfy_reproduces_router_id_failure_without_scoped_override(comfy_validator):
    validate, mappings, node_class = comfy_validator

    class WithoutModelOverride(node_class):
        VALIDATE_INPUTS = None

    mappings["LlamaCppGenerate"] = WithoutModelOverride
    (valid, errors, _), _ = validate({"prompt": "hello", "model": "qwen-vl-4B-Instruct"})
    assert valid is False
    assert any(error["type"] == "value_not_in_list" for error in errors)


@pytest.mark.parametrize("model", [12, 1.5, True, {}, {"__value__": 12}])
def test_installed_comfy_rejects_nonstring_model_constants(comfy_validator, model):
    validate, _, _ = comfy_validator
    (valid, errors, _), _ = validate({"prompt": "hello", "model": model})
    assert valid is False
    assert any(error["type"] == "custom_validation_failed" for error in errors)


@pytest.mark.parametrize(
    ("inputs", "error_type"),
    [
        ({"model": "router-id"}, "required_input_missing"),
        ({"prompt": "hello", "sampling_mode": "invalid"}, "value_not_in_list"),
        ({"prompt": "hello", "max_tokens": 0}, "value_smaller_than_min"),
        ({"prompt": "hello", "max_tokens": 1_048_577}, "value_bigger_than_max"),
        ({"prompt": "hello", "model": []}, "bad_linked_input"),
    ],
)
def test_installed_comfy_retains_other_field_validation(comfy_validator, inputs, error_type):
    validate, _, _ = comfy_validator
    (valid, errors, _), _ = validate(inputs)
    assert valid is False
    assert any(error["type"] == error_type for error in errors)


@pytest.mark.parametrize("compatible", [True, False])
def test_installed_comfy_preserves_model_links_and_link_type_checks(comfy_validator, compatible):
    validate, mappings, node_class = comfy_validator
    model_type = node_class.INPUT_TYPES()["optional"]["model"][0]

    class ModelSource:
        RETURN_TYPES = (model_type if compatible else "INT",)

        @classmethod
        def INPUT_TYPES(cls):
            return {"required": {}}

    mappings["ModelSource"] = ModelSource
    (valid, errors, _), _ = validate(
        {"prompt": "hello", "model": ["source", 0]},
        {"source": {"class_type": "ModelSource", "inputs": {}}},
    )
    assert valid is compatible
    if not compatible:
        assert any(error["type"] == "return_type_mismatch" for error in errors)


def test_installed_comfy_null_is_deferred_like_an_unresolved_link(comfy_validator):
    validate, _, _ = comfy_validator
    (valid, errors, _), _ = validate({"prompt": "hello", "model": None})
    assert (valid, errors) == (True, [])


@pytest.mark.parametrize("compatible", [True, False])
def test_installed_comfy_retains_unrelated_connection_type_validation(comfy_validator, compatible):
    validate, mappings, _ = comfy_validator

    class ConnectionSource:
        RETURN_TYPES = ("LLAMACPP_CONNECTION" if compatible else "IMAGE",)

        @classmethod
        def INPUT_TYPES(cls):
            return {"required": {}}

    mappings["ConnectionSource"] = ConnectionSource
    (valid, errors, _), _ = validate(
        {"prompt": "hello", "model": "router-id", "connection": ["source", 0]},
        {"source": {"class_type": "ConnectionSource", "inputs": {}}},
    )
    assert valid is compatible
    if not compatible:
        assert any(error["type"] == "return_type_mismatch" for error in errors)
