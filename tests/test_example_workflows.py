from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

EXAMPLE_ROOT = Path(__file__).resolve().parents[1] / "examples"
EXPECTED = {
    "direct-text.json": {
        "StartLlamaCppServer",
        "LlamaCppBasicPrompt",
        "LlamaCppPromptOutput",
        "LlamaCppTokenCount",
        "LlamaCppModelInfo",
        "LlamaCppReleaseRuntime",
    },
    "router-text.json": {
        "StartLlamaCppRouter",
        "LlamaCppListModels",
        "LlamaCppLoadModel",
        "LlamaCppBasicPrompt",
        "LlamaCppPromptOutput",
        "LlamaCppUnloadModel",
        "StopLlamaCppServer",
    },
    "vlm-image-to-prompt.json": {
        "LoadImage",
        "StartLlamaCppServer",
        "LlamaCppAdvPPPrompt",
        "LlamaCppPromptOutput",
        "LlamaCppReleaseRuntime",
    },
    "structured-output.json": {
        "StartLlamaCppServer",
        "LlamaCppStructuredOutput",
        "LlamaCppAdvPPPrompt",
        "LlamaCppPromptOutput",
        "LlamaCppReleaseRuntime",
    },
    "vram-handoff.json": {
        "StartLlamaCppServer",
        "LlamaCppBasicPrompt",
        "LlamaCppPromptOutput",
        "LlamaCppReleaseRuntime",
    },
}


def _load(name: str) -> dict:
    return json.loads((EXAMPLE_ROOT / name).read_text(encoding="utf-8"))


def _walk_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _walk_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_strings(child)


@pytest.mark.parametrize("name", EXPECTED)
def test_example_is_connected_current_comfy_workflow(name: str, node_package) -> None:
    workflow = _load(name)
    nodes = {node["id"]: node for node in workflow["nodes"]}
    node_types = {node["type"] for node in nodes.values()}
    links = workflow["links"]

    assert workflow["version"] == 0.4
    assert node_types == EXPECTED[name]
    assert links, "Examples must be connected workflows, not node inventories"
    assert len(nodes) == len(workflow["nodes"])
    assert len({link[0] for link in links}) == len(links)
    assert workflow["last_node_id"] >= max(nodes)
    assert workflow["last_link_id"] >= max(link[0] for link in links)
    assert workflow["extra"]["llamacpp_example"]["version"] == "0.3.0"

    for node_type in node_types - {"LoadImage"}:
        assert node_type in node_package.NODE_CLASS_MAPPINGS

    for link_id, source_id, output_index, target_id, input_index, link_type in links:
        source = nodes[source_id]
        target = nodes[target_id]
        source_output = source["outputs"][output_index]
        target_input = target["inputs"][input_index]

        assert link_id in (source_output.get("links") or [])
        assert target_input.get("link") == link_id
        assert source_output["type"] == link_type
        assert target_input["type"] in {link_type, "*"}


@pytest.mark.parametrize("name", EXPECTED)
def test_examples_are_portable_and_secret_free(name: str) -> None:
    workflow = _load(name)
    strings = tuple(_walk_strings(workflow))

    assert not any(re.search(r"(?i)\b[a-z]:[\\/]", value) for value in strings)
    assert not any("/home/" in value or "/mnt/" in value for value in strings)
    assert not any("bearer " in value.casefold() for value in strings)
    assert "LLAMACPP_API_KEY" in strings


def test_vlm_example_round_tripped_with_one_visible_image_socket() -> None:
    workflow = _load("vlm-image-to-prompt.json")
    node = next(node for node in workflow["nodes"] if node["type"] == "LlamaCppAdvPPPrompt")
    image_inputs = [
        item["name"] for item in node["inputs"] if re.fullmatch(r"image_\d+", item["name"])
    ]

    assert image_inputs == ["image_1"]
    assert next(item for item in node["inputs"] if item["name"] == "image_1")["link"] is not None


def test_structured_example_round_tripped_with_zero_image_sockets() -> None:
    workflow = _load("structured-output.json")
    node = next(node for node in workflow["nodes"] if node["type"] == "LlamaCppAdvPPPrompt")

    assert not any(re.fullmatch(r"image_\d+", item["name"]) for item in node["inputs"])
    structured = next(
        node for node in workflow["nodes"] if node["type"] == "LlamaCppStructuredOutput"
    )
    schema = json.loads(structured["widgets_values"][1])
    assert schema["properties"]["items"]["minItems"] == 2
    assert schema["additionalProperties"] is False
