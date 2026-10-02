"""Check the graph boundaries that protect literals and prevent LLM reruns."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_example_workflows import CORE_SCHEMAS, _expected_backend_inputs

EXAMPLES = Path(__file__).resolve().parents[1] / "example_workflows"
RECIPES = (
    "graph-structured-field",
    "graph-protected-literal-draft",
    "graph-frozen-render",
)


def load(name):
    return json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))


def ancestors(workflow, node_id):
    incoming = {}
    for _, source, _, target, _, _ in workflow["links"]:
        incoming.setdefault(target, set()).add(source)
    found, pending = set(), [node_id]
    while pending:
        for source in incoming.get(pending.pop(), ()):
            if source not in found:
                found.add(source)
                pending.append(source)
    return found


@pytest.mark.parametrize("name", RECIPES)
def test_recipe_links_are_consistent_and_acyclic(name):
    workflow = load(name)
    nodes = {node["id"]: node for node in workflow["nodes"]}
    assert len(nodes) == len(workflow["nodes"])
    assert workflow["version"] == 0.4
    assert workflow["last_node_id"] == max(nodes)
    links = {link[0]: link for link in workflow["links"]}
    assert len(links) == len(workflow["links"])
    assert workflow["last_link_id"] == max(links)
    for identifier, source, slot, target, input_slot, kind in links.values():
        output = nodes[source]["outputs"][slot]
        target_input = nodes[target]["inputs"][input_slot]
        assert identifier in output["links"]
        assert target_input["link"] == identifier
        assert output["type"] == target_input["type"] == kind
    for node in nodes.values():
        assert node["id"] not in ancestors(workflow, node["id"])
        for input_ in node["inputs"]:
            if input_["link"] is not None:
                assert input_["link"] in links
        for output in node["outputs"]:
            assert set(output["links"] or ()) <= links.keys()


def test_json_field_comes_from_response_and_inspector_gets_typed_result():
    workflow = load("graph-structured-field")
    nodes = {node["type"]: node for node in workflow["nodes"]}
    extract = nodes["JsonExtractString"]
    assert extract["widgets_values"] == ["", "positive_prompt"]
    schema = json.loads(nodes["LlamaCppStructuredOutput"]["widgets_values"][1])
    assert schema["required"] == ["positive_prompt"]
    assert schema["properties"]["positive_prompt"] == {"type": "string"}
    for kind, slot in [("JsonExtractString", 0), ("LlamaCppResult", 2)]:
        target = nodes[kind]
        link = next(link for link in workflow["links"] if link[3] == target["id"])
        assert link[1:3] == [nodes["LlamaCppGenerate"]["id"], slot]


def test_literal_is_appended_after_generation_and_never_sent_to_llm():
    workflow = load("graph-protected-literal-draft")
    nodes = {node["type"]: node for node in workflow["nodes"]}
    literal = nodes["PrimitiveStringMultiline"]
    concatenate = nodes["StringConcatenate"]
    generate = nodes["LlamaCppGenerate"]
    assert literal["widgets_values"] == ["Mira Vale, (blue coat:1.2), example_tag"]
    assert concatenate["widgets_values"][2] == ", "
    assert literal["id"] not in ancestors(workflow, generate["id"])
    assert {literal["id"], generate["id"]} <= ancestors(workflow, concatenate["id"])
    by_id = {node["id"]: node for node in workflow["nodes"]}
    literal_link = next(link for link in workflow["links"] if link[1] == literal["id"])
    assert by_id[literal_link[3]]["inputs"][literal_link[4]]["name"] == "string_b"


def test_frozen_render_has_only_core_nodes_and_uses_saved_string():
    workflow = load("graph-frozen-render")
    allowed = {
        "PrimitiveStringMultiline",
        "CheckpointLoaderSimple",
        "CLIPTextEncode",
        "EmptyLatentImage",
        "KSampler",
        "VAEDecode",
        "SaveImage",
    }
    assert {node["type"] for node in workflow["nodes"]} == allowed
    nodes = {node["id"]: node for node in workflow["nodes"]}
    literal = next(node for node in nodes.values() if node["type"] == "PrimitiveStringMultiline")
    assert literal["widgets_values"][0].strip()
    link = next(link for link in workflow["links"] if link[1] == literal["id"])
    encoder = nodes[link[3]]
    assert encoder["type"] == "CLIPTextEncode"
    assert encoder["inputs"][link[4]]["name"] == "text"
    save = next(node for node in nodes.values() if node["type"] == "SaveImage")
    assert literal["id"] in ancestors(workflow, save["id"])
    assert "LlamaCpp" not in json.dumps(workflow)


@pytest.mark.parametrize("name", RECIPES)
def test_new_recipes_preserve_current_pack_schemas(name, node_package):
    for node in load(name)["nodes"]:
        node_class = node_package.NODE_CLASS_MAPPINGS.get(node["type"])
        if node_class is None:
            inputs, outputs = CORE_SCHEMAS[node["type"]]
            assert [(item["name"], item["type"]) for item in node["inputs"]] == list(inputs)
            assert tuple(item["type"] for item in node["outputs"]) == outputs
            continue
        expected = _expected_backend_inputs(node, node_package)
        assert [(item["name"], item["type"]) for item in node["inputs"]] == expected
        assert tuple(item["type"] for item in node["outputs"]) == node_class.RETURN_TYPES
