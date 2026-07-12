from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
from PIL import Image

EXAMPLE_ROOT = Path(__file__).resolve().parents[1] / "example_workflows"
FIRST_RUN_THUMBNAILS = {"quick-text", "setup-check"}
LEGACY_EXAMPLE_HASHES = {
    "direct-text.json": "556ecb20b11185b38338fdb65daaf6b88e810b1fc75904cab4ddc08b8fb8c851",
    "quick-text.json": "35e6884c0f2ee8632059679aea295ec79cc124a8b69543ef45730ec62c3e2d2d",
    "router-text.json": "5cea06e9068c9a18a4e56a79d45e9ac86de514c40a6a8c178c70d152b8555121",
    "setup-check.json": "fba0e69ab7281f38bad391506a2fcfd80091f83ad4ea3eafe413130e39d149dc",
    "structured-output.json": "074f3b9931f7b9bbdbd7474be61a8bd0d50c12f24ff2987b8dc91ba5777ed1ab",
    "vlm-image-to-prompt.json": "891363a3771fb56dc8d2c8021f1c5e6cd0c6dc08d1eef17c2d1c15048a6b34ab",
    "vram-handoff.json": "94ae47604a00fa31bc2ea654f9fe04ba7107e877198eb5e39844089c98bef6bb",
}
CANONICAL_EXAMPLES = {
    "canonical-app-mode.json",
    "canonical-structured-json.json",
    "canonical-text.json",
    "canonical-vlm-image-understanding.json",
}
EXPECTED = {
    "setup-check.json": {
        "LlamaCppServerStatus",
        "LlamaCppPromptOutput",
    },
    "quick-text.json": {
        "StartLlamaCppServer",
        "LlamaCppBasicPrompt",
        "LlamaCppPromptOutput",
    },
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
    "canonical-text.json": {
        "StartLlamaCppServer",
        "LlamaCppTaskProfile",
        "LlamaCppGenerate",
        "LlamaCppPromptOutput",
    },
    "canonical-vlm-image-understanding.json": {
        "LoadImage",
        "StartLlamaCppServer",
        "LlamaCppTaskProfile",
        "LlamaCppGenerate",
        "LlamaCppPromptOutput",
    },
    "canonical-structured-json.json": {
        "StartLlamaCppServer",
        "LlamaCppTaskProfile",
        "LlamaCppStructuredOutput",
        "LlamaCppGenerate",
        "LlamaCppPromptOutput",
    },
    "canonical-app-mode.json": {
        "StartLlamaCppServer",
        "LlamaCppTaskProfile",
        "LlamaCppGenerate",
    },
}
CANONICAL_IMAGE_COUNTS = {
    "canonical-app-mode.json": 0,
    "canonical-structured-json.json": 0,
    "canonical-text.json": 0,
    "canonical-vlm-image-understanding.json": 1,
}
FREEFORM_PROFILE = {
    "description": "No prompt transformation.",
    "id": "freeform",
    "name": "Freeform",
    "prompt_prefix": "",
    "prompt_suffix": "",
    "schema_version": 1,
    "system_prompt": "",
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


def _widget_values(node: dict) -> dict[str, object]:
    """Map serialized primitive widgets, including Comfy's seed companion."""

    result = {}
    index = 0
    for input_spec in node["inputs"]:
        if "widget" not in input_spec:
            continue
        result[input_spec["name"]] = node["widgets_values"][index]
        index += 1
        if input_spec["name"] == "seed":
            result["seed_control_after_generate"] = node["widgets_values"][index]
            index += 1
    assert index == len(node["widgets_values"])
    return result


def _backend_input_type(input_spec) -> str:
    declared = input_spec[0]
    return "COMBO" if isinstance(declared, list) else declared


def _expected_backend_inputs(node: dict, node_package) -> list[tuple[str, str]]:
    schema = node_package.NODE_CLASS_MAPPINGS[node["type"]].INPUT_TYPES()
    fields = [
        (name, _backend_input_type(input_spec))
        for group in ("required", "optional")
        for name, input_spec in schema.get(group, {}).items()
    ]
    if node["type"] != "LlamaCppGenerate":
        return fields

    image_amount = int(_widget_values(node)["image_amount"])
    return [
        (name, input_type)
        for name, input_type in fields
        if not re.fullmatch(r"image_\d+", name) or int(name.removeprefix("image_")) <= image_amount
    ]


def _node(workflow: dict, node_type: str) -> dict:
    return next(node for node in workflow["nodes"] if node["type"] == node_type)


def test_example_directory_has_the_exact_curated_json_set() -> None:
    assert {path.name for path in EXAMPLE_ROOT.glob("*.json")} == set(EXPECTED)


@pytest.mark.parametrize("name,digest", sorted(LEGACY_EXAMPLE_HASHES.items()))
def test_released_examples_remain_byte_exact(name: str, digest: str) -> None:
    assert hashlib.sha256((EXAMPLE_ROOT / name).read_bytes()).hexdigest() == digest


def test_first_run_workflows_have_only_the_curated_thumbnail_pair() -> None:
    assert {path.stem for path in EXAMPLE_ROOT.glob("*.jpg")} == FIRST_RUN_THUMBNAILS


@pytest.mark.parametrize("stem", sorted(FIRST_RUN_THUMBNAILS))
def test_first_run_thumbnail_is_bounded_square_jpeg(stem: str) -> None:
    path = EXAMPLE_ROOT / f"{stem}.jpg"

    assert 0 < path.stat().st_size <= 256 * 1024
    with Image.open(path) as image:
        assert image.format == "JPEG"
        assert image.mode == "RGB"
        assert image.size == (768, 768)
        image.verify()


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
    expected_package_version = "0.4.0" if name in CANONICAL_EXAMPLES else "0.3.0"
    assert workflow["extra"]["llamacpp_example"]["version"] == expected_package_version

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


@pytest.mark.parametrize("name", sorted(CANONICAL_EXAMPLES))
def test_canonical_nodes_match_current_backend_input_order_and_types(
    name: str, node_package
) -> None:
    workflow = _load(name)
    for node in workflow["nodes"]:
        if node["type"] == "LoadImage":
            continue
        observed = [(item["name"], item["type"]) for item in node["inputs"]]
        assert observed == _expected_backend_inputs(node, node_package)


@pytest.mark.parametrize("name", sorted(CANONICAL_EXAMPLES))
def test_canonical_generate_serializes_only_contract_widgets(name: str) -> None:
    generate = _node(_load(name), "LlamaCppGenerate")
    widgets = _widget_values(generate)

    assert len(generate["widgets_values"]) == 25
    assert widgets["seed_control_after_generate"] in {
        "fixed",
        "increment",
        "decrement",
        "randomize",
    }
    assert widgets["sampling_mode"] == "default"
    assert widgets["partial_output_policy"] == "raise_error"


@pytest.mark.parametrize("name", EXPECTED)
def test_examples_are_portable_and_secret_free(name: str) -> None:
    workflow = _load(name)
    strings = tuple(_walk_strings(workflow))

    assert not any(re.search(r"(?i)\b[a-z]:[\\/]", value) for value in strings)
    assert not any("/home/" in value or "/mnt/" in value for value in strings)
    assert not any("bearer " in value.casefold() for value in strings)
    if "api_key_env" in strings:
        assert "LLAMACPP_API_KEY" in strings


@pytest.mark.parametrize("name", sorted(CANONICAL_EXAMPLES))
def test_canonical_examples_use_portable_placeholders_and_no_credentials(name: str) -> None:
    workflow = _load(name)
    start = _widget_values(_node(workflow, "StartLlamaCppServer"))
    generate = _widget_values(_node(workflow, "LlamaCppGenerate"))
    strings = tuple(_walk_strings(workflow))

    assert start["model"] == "(select an installed GGUF model)"
    assert start["binary_path"] == ""
    assert start["api_key_file"] == ""
    assert start["api_key_env"] == "LLAMACPP_API_KEY"
    assert start["mmproj"] == (
        "(select an installed projector)"
        if name == "canonical-vlm-image-understanding.json"
        else "(auto)"
    )
    assert generate["server_url"] == ""
    assert generate["model"] == "(use running model)"
    assert generate["api_key_env"] == "LLAMACPP_API_KEY"
    assert not any(re.search(r"(?i)\b(?:sk-[a-z0-9]|bearer\s+\S+)", text) for text in strings)
    assert not any(re.match(r"https?://", text, re.IGNORECASE) for text in strings)


@pytest.mark.parametrize("name", sorted(CANONICAL_EXAMPLES))
def test_canonical_profile_is_a_connected_portable_freeform_snapshot(name: str) -> None:
    workflow = _load(name)
    profile = _node(workflow, "LlamaCppTaskProfile")
    generate = _node(workflow, "LlamaCppGenerate")
    profile_input = next(item for item in generate["inputs"] if item["name"] == "profile")

    assert len(profile["widgets_values"]) == 1
    assert json.loads(profile["widgets_values"][0]) == FREEFORM_PROFILE
    assert profile_input["type"] == "LLAMACPP_PROFILE"
    assert profile_input["link"] is not None
    assert profile["outputs"][0]["type"] == "LLAMACPP_PROFILE"


@pytest.mark.parametrize("name,count", sorted(CANONICAL_IMAGE_COUNTS.items()))
def test_canonical_generate_round_trips_dynamic_image_contract(name: str, count: int) -> None:
    workflow = _load(name)
    generate = _node(workflow, "LlamaCppGenerate")
    image_inputs = [item for item in generate["inputs"] if re.fullmatch(r"image_\d+", item["name"])]

    assert _widget_values(generate)["image_amount"] == count
    assert [item["name"] for item in image_inputs] == [
        f"image_{index}" for index in range(1, count + 1)
    ]
    assert all(item["link"] is not None for item in image_inputs)


@pytest.mark.parametrize("name", sorted(CANONICAL_EXAMPLES))
def test_canonical_generate_has_typed_rich_result_output(name: str, node_package) -> None:
    generate = _node(_load(name), "LlamaCppGenerate")

    assert [(item["name"], item["type"]) for item in generate["outputs"]] == [
        ("response", "STRING"),
        ("thinking", "STRING"),
        ("result", "LLAMACPP_GENERATION_RESULT"),
    ]
    assert node_package.NODE_CLASS_MAPPINGS["LlamaCppGenerate"].OUTPUT_NODE is True


def test_canonical_structured_example_connects_a_strict_json_schema() -> None:
    workflow = _load("canonical-structured-json.json")
    structured = _node(workflow, "LlamaCppStructuredOutput")
    generate = _node(workflow, "LlamaCppGenerate")
    socket = next(item for item in generate["inputs"] if item["name"] == "structured_output")
    schema = json.loads(_widget_values(structured)["constraint"])

    assert socket["link"] is not None
    assert _widget_values(structured)["mode"] == "json_schema"
    assert _widget_values(structured)["strict"] is True
    assert schema["properties"]["keywords"]["minItems"] == 3
    assert schema["properties"]["keywords"]["maxItems"] == 5
    assert schema["additionalProperties"] is False


def test_canonical_app_mode_exposes_contract_widgets_transient_feedback_and_generate_output(
    node_package,
) -> None:
    workflow = _load("canonical-app-mode.json")
    nodes = {str(node["id"]): node for node in workflow["nodes"]}
    linear = workflow["extra"]["linearData"]

    assert workflow["extra"]["linearMode"] is True
    assert linear["outputs"] == [3]
    assert nodes["3"]["type"] == "LlamaCppGenerate"
    assert node_package.NODE_CLASS_MAPPINGS[nodes["3"]["type"]].OUTPUT_NODE is True

    serialized_entries = linear["inputs"][:8]
    transient_entries = linear["inputs"][8:]
    exposed_names = []
    for entry in serialized_entries:
        node_id, widget_name = entry[:2]
        assert type(node_id) is int
        assert any(
            item["name"] == widget_name and "widget" in item
            for item in nodes[str(node_id)]["inputs"]
        )
        schema = node_package.NODE_CLASS_MAPPINGS[nodes[str(node_id)]["type"]].INPUT_TYPES()
        input_spec = next(
            fields[widget_name]
            for fields in (schema.get("required", {}), schema.get("optional", {}))
            if widget_name in fields
        )
        assert input_spec[1].get("advanced") is not True
        exposed_names.append(widget_name)

    assert exposed_names == [
        "model",
        "binary_path",
        "prompt",
        "thinking_mode",
        "max_tokens",
        "sampling_mode",
        "seed",
        "release_after_generation",
    ]
    assert serialized_entries[2][2] == {"height": 220}
    assert transient_entries == [
        [3, "Generation Status"],
        [3, "Live Response", {"height": 220}],
    ]

    generate = nodes["3"]
    assert not any(
        item.get("name") in {"Generation Status", "Live Response"} for item in generate["inputs"]
    )
    assert len(generate["widgets_values"]) == 25


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
