"""Batch alignment and bounded preparation, without model or runtime access."""

from __future__ import annotations

import base64
import copy
import importlib
import io
import json
import sys
import types
from dataclasses import FrozenInstanceError
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import generation.captions as captions
from generation.captions import CaptionBatchSpec, CaptionItemSpec, caption_batch_outputs
from generation.contracts import (
    GenerationErrorInfo,
    GenerationReleaseInfo,
    GenerationResult,
    GenerationUsage,
)


def test_scalar_broadcast_exact_values_and_immutable_snapshot():
    spec = CaptionBatchSpec.from_inputs(3, " caption \n", 17)
    assert spec.item_count == 3
    assert spec.rows == tuple(CaptionItemSpec(str(i), " caption \n", 17) for i in range(3))
    with pytest.raises(FrozenInstanceError):
        spec.rows[0].seed = 3
    rows = list(spec.rows)
    frozen = CaptionBatchSpec(rows)
    rows.clear()
    assert frozen == spec


def test_exact_lists_and_json_scalar_overrides_never_repeat_last():
    spec = CaptionBatchSpec.from_inputs(
        2,
        "fallback",
        0,
        ids_json='["first image", "second image"]',
        prompts_json='["object", "color"]',
        seeds_json="[12, 23]",
    )
    assert spec.rows == (
        CaptionItemSpec("first image", "object", 12),
        CaptionItemSpec("second image", "color", 23),
    )
    broadcast = CaptionBatchSpec.from_inputs(
        2, "fallback", prompts_json='"one prompt"', seeds_json="5"
    )
    assert [(r.prompt, r.seed) for r in broadcast.rows] == [("one prompt", 5)] * 2
    assert CaptionBatchSpec.from_inputs(1, "fallback", prompts_json='""').rows[0].prompt == ""


@pytest.mark.parametrize("count", [True, False, 0, -1, 33, 1.0, "2", None])
def test_item_count_bounds(count):
    with pytest.raises(ValueError, match="1 to 32"):
        CaptionBatchSpec.from_inputs(count, "caption")
    assert CaptionBatchSpec.from_inputs(32, "caption").item_count == 32


@pytest.mark.parametrize("field", ["ids_json", "prompts_json", "seeds_json"])
@pytest.mark.parametrize(
    "value", ["null", "[]", '["only one"]', "NaN", "Infinity", "{" + '"x":1,' + '"x":2}']
)
def test_invalid_or_misaligned_json_rejected(field, value):
    with pytest.raises((ValueError, TypeError)):
        CaptionBatchSpec.from_inputs(2, "caption", **{field: value})


@pytest.mark.parametrize(
    "value", ['["same","same"]', '["","valid"]', '[" ","valid"]', "[0,1]", '"name"', "[true,false]"]
)
def test_ids_require_unique_nonempty_text_in_an_exact_list(value):
    with pytest.raises((ValueError, TypeError)):
        CaptionBatchSpec.from_inputs(2, "caption", ids_json=value)


@pytest.mark.parametrize("seed", [True, False, -1, 2**31, 0.0, "0", None])
def test_seed_rejects_bool_coercion_and_out_of_range(seed):
    with pytest.raises(ValueError):
        CaptionBatchSpec.from_inputs(1, "caption", seed)
    with pytest.raises(ValueError):
        CaptionBatchSpec.from_inputs(1, "caption", seeds_json=json.dumps(seed))


def test_bounded_utf8_json_prompts_and_ids(monkeypatch):
    with pytest.raises(ValueError, match="UTF-8"):
        CaptionBatchSpec.from_inputs(1, "\ud800")
    with pytest.raises(ValueError, match="UTF-8"):
        CaptionBatchSpec.from_inputs(1, "caption", ids_json='["\\ud800"]')
    with pytest.raises(ValueError, match="128"):
        CaptionBatchSpec.from_inputs(1, "caption", ids_json=json.dumps(["x" * 129]))
    with pytest.raises((TypeError, ValueError)):
        CaptionBatchSpec.from_inputs(1, "caption", prompts_json="[" * 2000 + "]" * 2000)
    monkeypatch.setattr(captions, "MAX_CAPTION_INPUT_JSON_BYTES", 8)
    with pytest.raises(ValueError, match="UTF-8 bytes"):
        CaptionBatchSpec.from_inputs(1, "caption", prompts_json='"漢漢漢"')
    monkeypatch.setattr(captions, "MAX_REQUEST_TEXT_CHARS", 4)
    with pytest.raises(ValueError, match="4 characters"):
        CaptionBatchSpec.from_inputs(1, "12345")


@pytest.fixture
def node_module(node_package, monkeypatch):
    module = importlib.import_module(f"{node_package.__name__}.nodes.captions")
    monkeypatch.setattr(module, "_interrupt_check", lambda: False)
    return module


def test_image_slices_precede_cpu_copy_and_preserve_order(node_module):
    values = np.zeros((3, 2, 2, 3), dtype=np.float32)
    for index in range(3):
        values[index, :, :, index] = 1
    events = []

    class Tensor:
        def __init__(self, array):
            self.array = array
            self.shape = array.shape

        def __getitem__(self, item):
            events.append(("slice", self.shape[0], item.start, item.stop))
            return Tensor(self.array[item])

        def detach(self):
            return self

        def cpu(self):
            events.append(("cpu", self.shape[0]))
            assert self.shape[0] == 1
            return self

        def numpy(self):
            return self.array

    tensor = Tensor(values)
    assert node_module._image_shape(tensor) == values.shape
    assert not events
    urls = node_module._prepare_images(tensor, values.shape)
    pixels = []
    for url in urls:
        with Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))) as image:
            pixels.append(image.getpixel((0, 0)))
    assert pixels == [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
    assert [event for event in events if event[0] == "cpu"] == [("cpu", 1)] * 3
    assert events[0] == ("slice", 3, 0, 1)


@pytest.mark.parametrize(
    "shape", [(33, 1, 1, 3), (0, 1, 1, 3), (1, 1, 2), (1, 0, 3), (1, 1), (True,)]
)
def test_invalid_shapes_fail_without_cpu_transfer(node_module, shape):
    class Tensor:
        def cpu(self):
            raise AssertionError("must not transfer")

    tensor = Tensor()
    tensor.shape = shape
    with pytest.raises(ValueError):
        node_module._image_shape(tensor)
    with pytest.raises(ValueError):
        node_module._image_shape(object())


def test_cancellation_and_encoded_budget_stop_before_next_frame(node_module, monkeypatch):
    images = np.zeros((3, 1, 1, 3))
    encoded = []

    def encode(*args, **kwargs):
        encoded.append(True)
        return ["123456789"]

    monkeypatch.setattr(node_module, "image_tensor_to_data_urls_bounded", encode)

    def cancelled():
        if encoded:
            raise InterruptedError("stop between images")

    with pytest.raises(InterruptedError):
        node_module._prepare_images(images, images.shape, cancelled)
    assert len(encoded) == 1
    encoded.clear()
    monkeypatch.setattr(node_module, "MAX_CAPTION_IMAGE_BYTES", 16)
    with pytest.raises(ValueError, match="16 encoded"):
        node_module._prepare_images(images, images.shape)
    assert len(encoded) == 2


def test_node_forwards_one_deferred_preparation_and_canonical_controls(node_module, monkeypatch):
    images = np.zeros((2, 1, 1, 3))
    calls = []

    class Executor:
        def captions(self, prompt, **kwargs):
            assert not calls
            calls.append((prompt, kwargs))
            assert len(kwargs["prepare_images"](lambda: None)) == 2
            return (["red", "blue"], '{"state":"complete"}')

    monkeypatch.setattr(node_module, "CanonicalGenerationExecutor", Executor)
    result = node_module.LlamaCppCaptions().caption(
        images,
        "caption",
        ids_json='["a","b"]',
        seeds_json="[5,6]",
        request_timeout=12,
        partial_output_policy="raise_error",
        release_after_generation=True,
    )
    assert result["result"] == (["red", "blue"], '{"state":"complete"}')
    assert result["ui"]["text"] == ('{"state":"complete"}',)
    prompt, call = calls[0]
    assert prompt == "caption"
    assert call["item_count"] == 2
    assert call["ids_json"] == '["a","b"]'
    assert call["request_timeout"] == 12
    assert call["release_after_generation"] is True
    assert call["partial_output_policy"] == "raise_error"
    assert node_module.LlamaCppCaptions.OUTPUT_IS_LIST == (True, False)


def test_node_alignment_failure_precedes_executor_and_image_conversion(node_module, monkeypatch):
    def forbidden():
        raise AssertionError("invalid group must not instantiate executor")

    monkeypatch.setattr(node_module, "CanonicalGenerationExecutor", forbidden)
    with pytest.raises(ValueError, match="exactly 2"):
        node_module.LlamaCppCaptions().caption(np.zeros((2, 1, 1, 3)), "caption", seeds_json="[1]")


def test_node_schema_excludes_multiimage_and_history_modes(node_module):
    schema = node_module.LlamaCppCaptions.INPUT_TYPES()
    assert list(schema["required"]) == ["images", "prompt"]
    assert schema["optional"]["partial_output_policy"][1]["default"] == "raise_error"
    assert {"image_amount", "include_image_batch", "image_1", "messages"}.isdisjoint(
        schema["optional"]
    )
    assert {"ids_json", "prompts_json", "seeds_json"}.issubset(schema["optional"])
    assert schema["hidden"] == node_module.LlamaCppGenerate.INPUT_TYPES()["hidden"]


@pytest.mark.parametrize("broken", [False, True])
def test_native_progress_is_lazy_and_optional(node_module, monkeypatch, broken):
    updates = []

    class ProgressBar:
        def __init__(self, total):
            updates.append(("init", total))
            if broken:
                raise RuntimeError("unavailable progress hook")

        def update_absolute(self, completed, total):
            updates.append((completed, total))

    monkeypatch.setitem(sys.modules, "comfy.utils", types.SimpleNamespace(ProgressBar=ProgressBar))

    class Executor:
        def captions(self, prompt, **kwargs):
            if broken:
                assert kwargs["progress"] is None
            else:
                for completed in range(3):
                    kwargs["progress"](completed, 2)
            return (["first", "second"], "{}")

    monkeypatch.setattr(node_module, "CanonicalGenerationExecutor", Executor)
    assert updates == []
    node_module.LlamaCppCaptions().caption(np.zeros((2, 1, 1, 3)), "caption")
    assert updates == ([("init", 2)] if broken else [("init", 2), (0, 2), (1, 2), (2, 2)])


def test_example_has_aligned_ids_strict_release_and_current_schemas(node_module, node_package):
    path = Path(__file__).resolve().parents[1] / "example_workflows/caption-batch-aligned.json"
    workflow = json.loads(path.read_text(encoding="utf-8"))
    nodes = {node["id"]: node for node in workflow["nodes"]}
    core = {"LoadImage": ("image", "upload"), "ImageBatch": ("image1", "image2")}
    classes = {**node_package.NODE_CLASS_MAPPINGS, "LlamaCppCaptions": node_module.LlamaCppCaptions}
    for node in nodes.values():
        if node["type"] in classes:
            schema = classes[node["type"]].INPUT_TYPES()
            expected = list(schema.get("required", {})) + list(schema.get("optional", {}))
            assert [value["name"] for value in node["inputs"]] == expected
        else:
            assert tuple(value["name"] for value in node["inputs"]) == core[node["type"]]
    for identifier, source, slot, target, input_slot, kind in workflow["links"]:
        assert identifier in nodes[source]["outputs"][slot]["links"]
        assert identifier == nodes[target]["inputs"][input_slot]["link"]
        assert nodes[source]["outputs"][slot]["type"] == kind
        assert nodes[target]["inputs"][input_slot]["type"] == kind
    caption = next(node for node in nodes.values() if node["type"] == "LlamaCppCaptions")
    values = iter(caption["widgets_values"])
    widgets = {}
    schema = node_module.LlamaCppCaptions.INPUT_TYPES()
    for name, definition in {**schema["required"], **schema["optional"]}.items():
        kind, options = definition
        if isinstance(kind, list) or kind in {"STRING", "INT", "FLOAT", "BOOLEAN"}:
            widgets[name] = next(values)
            if options.get("control_after_generate"):
                assert next(values) == "fixed"
    assert list(values) == []
    assert widgets["release_after_generation"] is True
    assert widgets["partial_output_policy"] == "raise_error"
    spec = CaptionBatchSpec.from_inputs(
        2,
        widgets["prompt"],
        widgets["seed"],
        ids_json=widgets["ids_json"],
        prompts_json=widgets["prompts_json"],
        seeds_json=widgets["seeds_json"],
    )
    assert [(row.item_id, row.seed) for row in spec.rows] == [("first", 101), ("second", 202)]


def result_row(item, *, state="complete", response="A blue notebook. 漢字"):
    error = None if state == "complete" else GenerationErrorInfo("transport", "stream stopped")
    result = GenerationResult(
        state=state,
        response=response,
        thinking="Separate thinking.\n",
        requested_model="exact/model.gguf",
        effective_model="exact/model.gguf",
        profile_id="freeform",
        profile_sha256="a" * 64,
        seed=item.seed,
        image_count=1,
        structured_output_kind=None,
        terminal=state == "complete",
        done_received=state == "complete",
        finish_reason="stop" if state == "complete" else None,
        usage=GenerationUsage(prompt_tokens=41, completion_tokens=7, total_tokens=48),
        error=error,
    )
    return {
        "item_id": item.item_id,
        "seed": item.seed,
        "state": "complete" if state == "complete" else "failed",
        "response": result.response,
        "thinking": result.thinking,
        "result": result.as_dict(),
        "error": None
        if error is None
        else {"category": error.category.value, "message": error.message},
    }


def unattempted_row(item):
    return {
        "item_id": item.item_id,
        "seed": item.seed,
        "state": "not_attempted",
        "response": "",
        "thinking": "",
        "result": None,
        "error": None,
    }


def test_group_serializer_retains_all_text_usage_stream_and_separate_release():
    spec = CaptionBatchSpec.from_inputs(2, "caption", seeds_json="[11,22]")
    rows = [result_row(item) for item in spec.rows]
    original = copy.deepcopy(rows)
    release = GenerationReleaseInfo(
        policy="release_after_generation",
        status="released",
        mode="direct",
        operation_id="group-release",
        terminal=True,
        success=True,
        released_models=("exact/model.gguf",),
    ).as_dict()
    outputs, serialized = caption_batch_outputs(
        spec, rows, state="complete", release=release, warnings=["example warning"]
    )
    assert outputs == [row["response"] for row in rows]
    decoded = json.loads(serialized)
    assert decoded == {
        "schema_version": 1,
        "operation": "captions",
        "state": "complete",
        "rows": original,
        "release": release,
        "warnings": ["example warning"],
    }
    assert rows == original
    assert decoded["rows"][0]["result"]["release"]["policy"] == "reuse"
    assert decoded["release"]["policy"] == "release_after_generation"
    assert decoded["release"]["driver_memory_verified"] is False


@pytest.mark.parametrize("state", ["partial", "failed", "cancelled"])
def test_noncomplete_groups_preserve_alignment_and_partial_evidence_without_caption_promotion(
    state,
):
    spec = CaptionBatchSpec.from_inputs(3, "caption", ids_json='["first","broken","later"]')
    rows = [
        result_row(spec.rows[0]),
        result_row(spec.rows[1], state="partial", response="uncompleted text"),
        unattempted_row(spec.rows[2]),
    ]
    outputs, serialized = caption_batch_outputs(
        spec, rows, state=state, release={"status": "not_requested"}
    )
    assert outputs == [rows[0]["response"], "", ""]
    decoded = json.loads(serialized)
    assert decoded["state"] == state
    assert [row["item_id"] for row in decoded["rows"]] == ["first", "broken", "later"]
    assert decoded["rows"][1]["response"] == "uncompleted text"
    assert decoded["rows"][1]["result"]["partial"] is True
    assert decoded["rows"][2]["result"] is None


def test_serializer_leaves_strict_policy_and_release_failure_to_coordinator():
    spec = CaptionBatchSpec.from_inputs(1, "caption")
    row = result_row(spec.rows[0])
    output, serialized = caption_batch_outputs(
        spec, [row], state="failed", release={"success": False, "status": "failed"}
    )
    assert output == [row["response"]]
    assert json.loads(serialized)["state"] == "failed"


@pytest.mark.parametrize("change", ["short", "long", "reordered", "seed", "bool_seed"])
def test_serializer_rejects_lost_shifted_or_misattributed_rows(change):
    spec = CaptionBatchSpec.from_inputs(2, "caption", seeds_json="[11,22]")
    rows = [result_row(item) for item in spec.rows]
    if change == "short":
        rows.pop()
    elif change == "long":
        rows.append(rows[0])
    elif change == "reordered":
        rows.reverse()
    elif change == "seed":
        rows[0]["seed"] = 22
    else:
        rows[0]["seed"] = True
    with pytest.raises(ValueError):
        caption_batch_outputs(spec, rows, state="complete", release={})


@pytest.mark.parametrize(
    "change",
    [
        "missing_result",
        "empty_response",
        "response_mismatch",
        "thinking_mismatch",
        "result_seed",
        "terminal",
        "model",
        "image_count",
        "state",
        "extra_field",
    ],
)
def test_serializer_rejects_false_complete_evidence(change):
    spec = CaptionBatchSpec.from_inputs(1, "caption")
    row = result_row(spec.rows[0])
    if change == "missing_result":
        row["result"] = None
    elif change == "empty_response":
        row["response"] = ""
    elif change == "response_mismatch":
        row["response"] = "Different caption"
    elif change == "thinking_mismatch":
        row["thinking"] = "Different thinking"
    elif change == "result_seed":
        row["result"]["seed"] = 44
    elif change == "terminal":
        row["result"]["terminal"] = False
    elif change == "model":
        row["result"]["effective_model"] = None
    elif change == "image_count":
        row["result"]["image_count"] = 2
    elif change == "state":
        row["state"] = "not_attempted"
    else:
        row["filename"] = "invented.jpg"
    with pytest.raises(ValueError):
        caption_batch_outputs(spec, [row], state="complete", release={})


def test_serializer_rejects_failed_error_mismatch_and_unattempted_fabrication():
    spec = CaptionBatchSpec.from_inputs(1, "caption")
    row = result_row(spec.rows[0], state="partial")
    row["error"]["message"] = "Different failure"
    with pytest.raises(ValueError, match="error must match"):
        caption_batch_outputs(spec, [row], state="failed", release={})
    row = unattempted_row(spec.rows[0])
    row["error"] = {"category": "transport", "message": "Never attempted"}
    with pytest.raises(ValueError, match="unattempted"):
        caption_batch_outputs(spec, [row], state="failed", release={})
    with pytest.raises(ValueError, match="every row"):
        caption_batch_outputs(spec, [unattempted_row(spec.rows[0])], state="complete", release={})


def test_serializer_enforces_exact_utf8_budget_and_json_safe_metadata(monkeypatch):
    spec = CaptionBatchSpec.from_inputs(1, "caption")
    rows = [result_row(spec.rows[0])]
    _, serialized = caption_batch_outputs(spec, rows, state="complete", release={})
    byte_count = len(serialized.encode("utf-8"))
    assert byte_count > len(serialized)
    monkeypatch.setattr(captions, "MAX_CAPTION_RESULT_BYTES", byte_count)
    assert caption_batch_outputs(spec, rows, state="complete", release={})[1] == serialized
    monkeypatch.setattr(captions, "MAX_CAPTION_RESULT_BYTES", byte_count - 1)
    with pytest.raises(ValueError, match="serialized UTF-8 bytes"):
        caption_batch_outputs(spec, rows, state="complete", release={})
    monkeypatch.setattr(captions, "MAX_CAPTION_RESULT_BYTES", 8 * 1024 * 1024)
    with pytest.raises(ValueError):
        caption_batch_outputs(spec, rows, state="complete", release={"elapsed": float("nan")})
    with pytest.raises(ValueError, match="UTF-8"):
        caption_batch_outputs(spec, rows, state="complete", release={}, warnings=["\ud800"])


def test_oversized_envelope_fails_before_canonical_result_revalidation(monkeypatch):
    assert captions.MAX_CAPTION_RESULT_BYTES == 8 * 1024 * 1024
    spec = CaptionBatchSpec.from_inputs(1, "caption")
    row = result_row(spec.rows[0], response="x" * (4 * 1024 * 1024))

    def forbidden(*args, **kwargs):
        raise AssertionError("oversized envelope must fail before canonical revalidation")

    monkeypatch.setattr(captions.GenerationResult, "from_dict", forbidden)
    with pytest.raises(ValueError, match="serialized UTF-8 bytes"):
        caption_batch_outputs(spec, [row], state="complete", release={})
