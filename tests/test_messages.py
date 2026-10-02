from __future__ import annotations

import importlib
import json
from dataclasses import FrozenInstanceError

import pytest
from test_canonical_generation import executor
from test_generation_contracts import request_spec

from generation.contracts import GenerationRequestSpec, GenerationResult
from generation.execution import CanonicalGenerationError
from generation.messages import MAX_MESSAGES, ConversationMessages, TextMessage
from generation.profiles import TaskProfileSnapshot


def history() -> ConversationMessages:
    return ConversationMessages().append("user", "  name a color\n").append("assistant", "blue\n")


def test_exact_role_bytes_roundtrip_and_no_mutation():
    original = history()
    expanded = original.append("user", "next\u00a0\n")
    assert len(original.messages) == 2
    assert len(expanded.messages) == 3
    assert ConversationMessages.from_json(expanded.to_json()) == expanded
    with pytest.raises(FrozenInstanceError):
        original.messages[0].content = "edited"


def test_few_shot_final_payload_and_result_roundtrip_are_exact():
    runner, manager, client = executor()
    earlier = history()
    _, _, result = runner.generate(" current \n", messages=earlier, system_prompt="rules\n")
    assert client.calls[0]["payload"]["messages"] == [
        {"role": "system", "content": "rules\n"},
        {"role": "user", "content": "  name a color\n"},
        {"role": "assistant", "content": "blue\n"},
        {"role": "user", "content": " current \n"},
    ]
    assert result.messages_sha256 == earlier.content_sha256
    assert result.message_count == 2
    assert GenerationResult.from_json(result.to_json()) == result
    request = request_spec(messages=earlier)
    assert GenerationRequestSpec.from_json(request.to_json()) == request
    runner.generate("again", messages=earlier)
    assert len(client.calls[1]["payload"]["messages"]) == 3
    assert len(earlier.messages) == 2
    assert len(manager.runtime_service.finish_calls) == 2


def test_image_stays_in_current_turn_and_history_system_is_unique():
    runner, _, client = executor()
    earlier = ConversationMessages().append("system", "exact rules").append("user", "before")
    runner.generate("now", messages=earlier, images=["data:image/png;base64,abcd"])
    payload = client.calls[0]["payload"]["messages"]
    assert payload[:-1] == [m.as_dict() for m in earlier.messages]
    assert payload[-1]["role"] == "user"
    assert payload[-1]["content"] == [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,abcd"}},
        {"type": "text", "text": "now"},
    ]


@pytest.mark.parametrize(
    "system,profile",
    [
        ("other", None),
        ("", TaskProfileSnapshot("rules", "Rules", system_prompt="other")),
    ],
)
def test_conflicting_system_sources_fail_before_admission(system, profile):
    runner, manager, client = executor()
    earlier = ConversationMessages().append("system", "rules")
    with pytest.raises(CanonicalGenerationError, match="conflicts"):
        runner.generate("now", messages=earlier, system_prompt=system, profile=profile)
    assert not manager.runtime_service.begin_calls
    assert not client.calls


def test_history_absent_keeps_legacy_request_and_payload_shape():
    runner, _, client = executor()
    _, _, result = runner.generate("old workflow")
    request = request_spec()
    assert "messages" not in request.as_dict()
    assert "messages_sha256" not in result.as_dict()
    assert client.calls[0]["payload"]["messages"] == [{"role": "user", "content": "old workflow"}]
    assert GenerationRequestSpec.from_dict(request.as_dict()) == request


@pytest.mark.parametrize(
    "text",
    [
        '{"schema_version":true,"messages":[]}',
        '{"schema_version":1,"schema_version":1,"messages":[]}',
        '{"schema_version":1,"messages":[],"tools":[]}',
        '{"schema_version":1,"messages":[{"role":"tool","content":"x"}]}',
        '{"schema_version":1,"messages":[{"role":"user","content":[{}]}]}',
        '{"schema_version":1,"messages":[{"role":"user","content":"x","name":"x"}]}',
        '{"schema_version":1,"messages":[{"role":"user","content":"x"},{"role":"system","content":"x"}]}',
        '{"schema_version":1,"messages":NaN}',
    ],
)
def test_malformed_history_is_rejected(text):
    with pytest.raises((ValueError, TypeError)):
        ConversationMessages.from_json(text)


def test_history_bounds_apply_to_count_utf8_and_escaped_json():
    with pytest.raises(ValueError, match="messages"):
        ConversationMessages((TextMessage("user", "x"),) * (MAX_MESSAGES + 1))
    with pytest.raises(ValueError, match="serialized bytes"):
        ConversationMessages((TextMessage("user", "\u00e9" * 200_000),) * 3)
    with pytest.raises(ValueError, match="serialized bytes"):
        ConversationMessages((TextMessage("user", "\0" * 200_000),))
    with pytest.raises(ValueError):
        TextMessage("user", "\ud800")


def test_nodes_build_and_restore_explicit_history(node_package):
    module = importlib.import_module(f"{node_package.__name__}.nodes.messages")
    node = module.LlamaCppMessage()
    earlier, text = node.append("user", "  exact\n")
    (restored,) = module.LlamaCppMessages().load(text)
    final, _ = node.append("assistant", "reply", restored)
    assert json.loads(final.to_json())["messages"] == [
        {"role": "user", "content": "  exact\n"},
        {"role": "assistant", "content": "reply"},
    ]
    assert len(earlier.messages) == 1
    assert module.LlamaCppMessage.INPUT_TYPES()["optional"]["messages"][0] == "LLAMACPP_MESSAGES"
