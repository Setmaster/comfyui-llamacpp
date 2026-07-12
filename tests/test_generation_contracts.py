from __future__ import annotations

import dataclasses
import importlib
import inspect
import json
import math

import pytest

import generation.contracts as contract_module
from generation.contracts import (
    ErrorCategory,
    GenerationErrorInfo,
    GenerationReleaseInfo,
    GenerationRequestSpec,
    GenerationResult,
    GenerationState,
    GenerationTiming,
    GenerationUsage,
    PartialOutputPolicy,
    ReleasePolicy,
    SamplingMode,
    SamplingSettings,
    ThinkingMode,
)
from generation.profiles import FREEFORM_PROFILE


def connection_snapshot() -> dict:
    return {
        "schema_version": 1,
        "server_url": "",
        "model": "",
        "api_key_env": "LLAMACPP_API_KEY",
        "verify_tls": True,
        "request_timeout": 300,
    }


def request_spec(**overrides) -> GenerationRequestSpec:
    values = {
        "connection": connection_snapshot(),
        "prompt": "Describe the scene.",
        "profile_id": FREEFORM_PROFILE.profile_id,
        "profile_sha256": FREEFORM_PROFILE.content_sha256,
    }
    values.update(overrides)
    return GenerationRequestSpec(**values)


def complete_result(**overrides) -> GenerationResult:
    values = {
        "state": GenerationState.COMPLETE,
        "response": "done",
        "thinking": "",
        "requested_model": None,
        "effective_model": "local-model",
        "profile_id": FREEFORM_PROFILE.profile_id,
        "profile_sha256": FREEFORM_PROFILE.content_sha256,
        "seed": 7,
        "image_count": 0,
        "structured_output_kind": None,
        "terminal": True,
    }
    values.update(overrides)
    return GenerationResult(**values)


def test_enum_wire_values_are_stable_and_lowercase():
    assert [item.value for item in ThinkingMode] == ["auto", "off", "on"]
    assert [item.value for item in SamplingMode] == ["default", "custom"]
    assert [item.value for item in PartialOutputPolicy] == [
        "raise_error",
        "return_marked_partial",
    ]
    assert [item.value for item in ReleasePolicy] == [
        "reuse",
        "release_after_generation",
    ]
    assert {item.value for item in ErrorCategory} == {
        "invalid_request",
        "runtime_unavailable",
        "model_missing",
        "capability_unsupported",
        "authentication",
        "tls",
        "timeout",
        "transport",
        "server",
        "protocol",
        "structured_json",
        "cancelled",
        "release_failed",
    }


def test_sampling_settings_are_frozen_and_round_trip_deterministically():
    settings = SamplingSettings(
        mode="custom",
        temperature=0.25,
        top_p=0.8,
        top_k=12,
        min_p=0.1,
        repeat_penalty=1.05,
        presence_penalty=-0.1,
        frequency_penalty=0.2,
    )
    restored = SamplingSettings.from_json(settings.to_json())
    assert restored == settings
    assert settings.to_json() == restored.to_json()
    assert json.loads(settings.to_json())["schema_version"] == 1
    with pytest.raises(dataclasses.FrozenInstanceError):
        settings.top_k = 99


@pytest.mark.parametrize("field_name", ["temperature", "top_p", "min_p"])
def test_sampling_settings_reject_nonfinite_numbers(field_name):
    values = {field_name: math.inf}
    with pytest.raises(ValueError, match="finite"):
        SamplingSettings(**values)


def test_request_round_trip_is_json_safe_and_retains_no_image_data_or_secret():
    request = request_spec(
        system_prompt="Be concise.",
        requested_model="router/model.gguf",
        thinking_mode="off",
        sampling=SamplingSettings(mode="custom"),
        stop_sequences=("END", "STOP"),
        image_count=10,
        structured_output_kind="json_schema",
        token_bias_count=3,
        release_policy="release_after_generation",
        partial_output_policy="return_marked_partial",
    )
    encoded = request.to_json()
    restored = GenerationRequestSpec.from_json(encoded)

    assert restored == request
    assert restored.to_json() == encoded
    assert "data:image" not in encoded
    assert "api_key" not in encoded.replace("api_key_env", "")
    assert "LLAMACPP_API_KEY" in encoded
    assert request.connection == connection_snapshot()
    with pytest.raises(TypeError):
        request.connection["server_url"] = "changed"


def test_request_rejects_connection_secret_fields_and_unknown_contract_fields():
    connection = connection_snapshot()
    connection["api_key"] = "secret"
    with pytest.raises(ValueError, match="unexpected"):
        request_spec(connection=connection)

    serialized = request_spec().as_dict()
    serialized["image_data"] = ["data:image/png;base64,secret"]
    with pytest.raises(ValueError, match="unexpected"):
        GenerationRequestSpec.from_dict(serialized)


@pytest.mark.parametrize(
    ("server_url", "message"),
    (
        ("http://user:secret@localhost:8080", "must not contain credentials"),
        ("ftp://localhost/model", "absolute HTTP or HTTPS"),
        ("http://localhost:8080?api_key=secret", "query or fragment"),
    ),
)
def test_request_connection_snapshot_rejects_secret_bearing_or_invalid_urls(
    server_url,
    message,
):
    connection = connection_snapshot()
    connection["server_url"] = server_url
    with pytest.raises(ValueError, match=message):
        request_spec(connection=connection)


@pytest.mark.parametrize(
    "api_key_env",
    ("LLAMACPP_API_KEY", "LLAMACPP_API_KEY_STUDIO", "LLAMACPP_API_KEY_GPU_2", ""),
)
def test_request_connection_snapshot_accepts_only_canonical_key_names(api_key_env):
    connection = connection_snapshot()
    connection["api_key_env"] = api_key_env
    assert request_spec(connection=connection).connection["api_key_env"] == api_key_env


@pytest.mark.parametrize(
    "api_key_env",
    (
        "raw bearer token",
        "OPENAI_API_KEY",
        "LLAMACPP_API_KEY_lowercase",
        "LLAMACPP_API_KEY-SHARED",
    ),
)
def test_request_connection_snapshot_rejects_arbitrary_key_names(api_key_env):
    connection = connection_snapshot()
    connection["api_key_env"] = api_key_env
    with pytest.raises(ValueError, match="LLAMACPP_API_KEY"):
        request_spec(connection=connection)


def test_request_validates_profile_hash_and_structured_kind():
    with pytest.raises(ValueError, match="profile_sha256"):
        request_spec(profile_sha256="not-a-hash")
    with pytest.raises(ValueError, match="structured_output_kind"):
        request_spec(structured_output_kind="xml")
    with pytest.raises(ValueError, match="prompt must not be empty"):
        request_spec(prompt=" \n ")


def test_contract_json_rejects_duplicate_keys_and_nonfinite_constants():
    with pytest.raises(ValueError, match="duplicate JSON key"):
        SamplingSettings.from_json(
            '{"schema_version":1,"mode":"default","mode":"custom",'
            '"temperature":0.7,"top_p":0.9,"top_k":40,"min_p":0.05,'
            '"repeat_penalty":1.1,"presence_penalty":0.0,"frequency_penalty":0.0}'
        )


@pytest.mark.parametrize(
    "encoded",
    (
        SamplingSettings().to_json().encode("utf-16"),
        SamplingSettings().to_json().encode("utf-32"),
        SamplingSettings().to_json().encode("utf-8-sig"),
    ),
)
def test_contract_json_rejects_non_utf8_and_bom_documents(encoded):
    with pytest.raises(ValueError, match="UTF-8|BOM"):
        SamplingSettings.from_json(encoded)


@pytest.mark.parametrize(
    "factory",
    (
        lambda: request_spec(prompt="bad \ud800 prompt"),
        lambda: complete_result(response="bad \ud800 response"),
        lambda: GenerationErrorInfo(ErrorCategory.PROTOCOL, "bad \ud800 error"),
    ),
)
def test_contracts_reject_non_utf8_scalar_text(factory):
    with pytest.raises(ValueError, match="UTF-8"):
        factory()


def test_constructed_contract_enforces_aggregate_encoded_json_budget(monkeypatch):
    monkeypatch.setattr(contract_module, "MAX_RESULT_JSON_BYTES", 512)
    with pytest.raises(ValueError, match="generation result exceeds 512 bytes"):
        complete_result(response="x" * 500)

    monkeypatch.setattr(contract_module, "MAX_RESULT_JSON_BYTES", 4096)
    result = complete_result(response="round trip")
    assert GenerationResult.from_json(result.to_json()) == result
    with pytest.raises(ValueError, match="invalid JSON constant"):
        SamplingSettings.from_json(
            '{"schema_version":1,"mode":"default","temperature":NaN,'
            '"top_p":0.9,"top_k":40,"min_p":0.05,"repeat_penalty":1.1,'
            '"presence_penalty":0.0,"frequency_penalty":0.0}'
        )


@pytest.mark.parametrize(
    ("contract_type", "value"),
    (
        (SamplingSettings, SamplingSettings()),
        (GenerationRequestSpec, request_spec()),
        (GenerationUsage, GenerationUsage()),
        (GenerationTiming, GenerationTiming()),
        (
            GenerationErrorInfo,
            GenerationErrorInfo(ErrorCategory.PROTOCOL, "invalid response"),
        ),
        (GenerationReleaseInfo, GenerationReleaseInfo()),
        (GenerationResult, complete_result()),
    ),
)
def test_every_contract_rejects_boolean_schema_versions(contract_type, value):
    serialized = value.as_dict()
    serialized["schema_version"] = True
    with pytest.raises(TypeError, match="schema_version must be an integer"):
        contract_type.from_dict(serialized)


def test_usage_normalizes_only_known_integer_fields():
    usage = GenerationUsage.from_mapping(
        {
            "prompt_tokens": 4,
            "completion_tokens": 5,
            "total_tokens": 9,
            "arbitrary_server_body": {"secret": "ignored"},
        }
    )
    assert usage == GenerationUsage(4, 5, 9)
    assert "arbitrary_server_body" not in usage.as_dict()
    with pytest.raises(TypeError, match="integer"):
        GenerationUsage(prompt_tokens=True)


def test_result_round_trip_is_deterministic_and_contains_normalized_metadata():
    result = complete_result(
        response='{"answer":"yes"}',
        structured_output_kind="json_object",
        usage=GenerationUsage(12, 4, 16),
        finish_reason="stop",
        response_id="chatcmpl-local",
        chunks=5,
        done_received=True,
        timing=GenerationTiming(0.2, 1.1, 0.3, 1.4),
        release=GenerationReleaseInfo(
            policy="release_after_generation",
            status="complete",
            mode="router",
            target_model="router/model.gguf",
            request_id="request-1",
            operation_id="operation-1",
            scope="router_model",
            coalesced=True,
            superseded_by="global-release-1",
            driver_memory_verified=False,
            terminal=True,
            success=True,
            elapsed_seconds=0.3,
            released_models=("router/model.gguf",),
        ),
        warnings=("context limit unknown",),
    )
    encoded = result.to_json()
    restored = GenerationResult.from_json(encoded)

    assert restored == result
    assert restored.to_json() == encoded
    assert restored.success is True
    assert restored.partial is False
    assert restored.cancelled is False
    assert "raw" not in restored.as_dict()
    assert restored.release.as_dict() == {
        "schema_version": 1,
        "policy": "release_after_generation",
        "status": "complete",
        "mode": "router",
        "target_model": "router/model.gguf",
        "request_id": "request-1",
        "operation_id": "operation-1",
        "scope": "router_model",
        "coalesced": True,
        "superseded_by": "global-release-1",
        "driver_memory_verified": False,
        "terminal": True,
        "success": True,
        "elapsed_seconds": 0.3,
        "released_models": ["router/model.gguf"],
        "error": None,
    }


def test_partial_result_requires_explicit_normalized_error():
    error = GenerationErrorInfo(
        ErrorCategory.TIMEOUT,
        "generation exceeded its deadline",
        retryable=True,
    )
    partial = complete_result(
        state="partial",
        response="partial text",
        terminal=False,
        error=error,
    )
    assert partial.success is False
    assert partial.partial is True

    with pytest.raises(ValueError, match="must contain an error"):
        complete_result(state="partial", terminal=False)
    with pytest.raises(ValueError, match="cannot contain an error"):
        complete_result(error=error)


def test_result_rejects_tampered_derived_state_flags():
    value = complete_result().as_dict()
    value["success"] = False
    with pytest.raises(ValueError, match="does not match state"):
        GenerationResult.from_dict(value)


def test_failed_release_requires_a_bounded_normalized_error():
    error = GenerationErrorInfo(
        category="release_failed",
        message="selected model did not reach terminal unloaded state",
    )
    release = GenerationReleaseInfo(
        policy="release_after_generation",
        status="failed",
        mode="router",
        terminal=True,
        success=False,
        error=error,
    )
    assert GenerationReleaseInfo.from_dict(release.as_dict()) == release
    with pytest.raises(ValueError, match="must contain an error"):
        GenerationReleaseInfo(success=False)
    with pytest.raises(ValueError, match="must be terminal"):
        GenerationReleaseInfo(terminal=False)
    with pytest.raises(ValueError, match="independent device proof"):
        GenerationReleaseInfo(driver_memory_verified=True)


def test_nonterminal_accepted_release_round_trips_without_claiming_failure():
    release = GenerationReleaseInfo(
        policy="release_after_generation",
        status="accepted_continuing",
        mode="router",
        target_model="model-a",
        operation_id="operation-a",
        scope="router_model",
        terminal=False,
        success=False,
    )
    assert release.error is None
    assert GenerationReleaseInfo.from_dict(release.as_dict()) == release


def test_connection_constructor_contract_is_unchanged_and_round_trips(node_package):
    module = importlib.import_module(f"{node_package.__name__}.nodes.connection")
    profile_class = module.LlamaCppConnectionProfile

    parameters = list(inspect.signature(profile_class).parameters)
    assert parameters == [
        "server_url",
        "model",
        "api_key_env",
        "verify_tls",
        "request_timeout",
    ]
    profile = profile_class(
        "https://localhost:8080",
        "model.gguf",
        "LOCAL_KEY",
        False,
        42,
    )
    restored = profile_class.from_dict(profile.as_dict())
    assert restored == profile
    assert profile.as_dict() == {
        "schema_version": 1,
        "server_url": "https://localhost:8080",
        "model": "model.gguf",
        "api_key_env": "LOCAL_KEY",
        "verify_tls": False,
        "request_timeout": 42,
    }
    assert "secret" not in profile.as_dict()


def test_connection_snapshot_rejects_unknown_or_wrongly_typed_values(node_package):
    module = importlib.import_module(f"{node_package.__name__}.nodes.connection")
    profile_class = module.LlamaCppConnectionProfile
    value = connection_snapshot()
    value["authorization"] = "Bearer secret"
    with pytest.raises(ValueError, match="unexpected"):
        profile_class.from_dict(value)

    value = connection_snapshot()
    value["verify_tls"] = 1
    with pytest.raises(TypeError, match="Boolean"):
        profile_class.from_dict(value)
