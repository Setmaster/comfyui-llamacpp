"""The result adapter must preserve the authoritative result, including partials."""

from __future__ import annotations

import importlib
import json

import pytest


@pytest.fixture
def result_module(node_package):
    return importlib.import_module(f"{node_package.__name__}.nodes.result")


@pytest.fixture
def contracts(node_package):
    return importlib.import_module(f"{node_package.__name__}.generation.contracts")


@pytest.mark.parametrize("state", ["complete", "partial", "cancelled"])
def test_json_preserves_full_text_state_and_terminal_release(result_module, contracts, state):
    response = "A blue notebook. 漢字\n" * 2048
    thinking = "Separate reasoning.\n" * 1024
    error = (
        None
        if state == "complete"
        else contracts.GenerationErrorInfo(
            category="cancelled" if state == "cancelled" else "transport",
            message="Interrupted before completion",
        )
    )
    result = contracts.GenerationResult(
        state=state,
        response=response,
        thinking=thinking,
        requested_model="local/model.gguf",
        effective_model="local/model.gguf",
        profile_id="freeform",
        profile_sha256="a" * 64,
        seed=17,
        image_count=2,
        structured_output_kind=None,
        usage=contracts.GenerationUsage(prompt_tokens=71, completion_tokens=19, total_tokens=90),
        terminal=state == "complete",
        done_received=state == "complete",
        finish_reason="stop" if state == "complete" else None,
        release=contracts.GenerationReleaseInfo(
            policy="release_after_generation",
            status="released",
            mode="router",
            target_model="local/model.gguf",
            request_id="release-request",
            operation_id="release-operation",
            scope="model",
            terminal=True,
            success=True,
            elapsed_seconds=0.5,
            released_models=("local/model.gguf",),
        ),
        error=error,
        warnings=("Upstream cleanup was not confirmed",),
    )

    (serialized,) = result_module.LlamaCppResult().serialize(result)
    decoded = json.loads(serialized)

    assert decoded == result.as_dict()
    assert decoded["response"] == response
    assert decoded["thinking"] == thinking
    assert decoded["partial"] is (state != "complete")
    assert decoded["cancelled"] is (state == "cancelled")
    assert decoded["release"]["terminal"] is True
    assert decoded["release"]["driver_memory_verified"] is False
    assert contracts.GenerationResult.from_dict(decoded) == result


@pytest.mark.parametrize("value", [None, {}, {"schema_version": 99}, '{"state":"complete"}'])
def test_rejects_untyped_input_without_turning_errors_into_text(result_module, value):
    with pytest.raises(TypeError, match="typed generation result"):
        result_module.LlamaCppResult().serialize(value)


def test_registration_is_a_narrow_typed_to_string_adapter(node_package, result_module):
    node = node_package.NODE_CLASS_MAPPINGS["LlamaCppResult"]
    assert node is result_module.LlamaCppResult
    assert node.RETURN_TYPES == ("STRING",)
    assert node.RETURN_NAMES == ("result_json",)
    assert node.INPUT_TYPES()["required"]["result"][0] == "LLAMACPP_GENERATION_RESULT"
    assert not getattr(node, "OUTPUT_NODE", False)
