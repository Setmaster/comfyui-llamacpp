from __future__ import annotations

import dataclasses
import importlib
import inspect
import json

import pytest

from generation.profiles import (
    FREEFORM_PROFILE,
    FREEFORM_SNAPSHOT_JSON,
    MAX_PROFILE_COUNT,
    MAX_PROFILE_DESCRIPTION_CHARS,
    MAX_PROFILE_FILE_BYTES,
    MAX_PROFILE_ID_CHARS,
    MAX_PROFILE_NAME_CHARS,
    MAX_PROFILE_TEXT_CHARS,
    ProfileValidationError,
    TaskProfileSnapshot,
    apply_task_profile,
    load_profiles,
    parse_profile_snapshot,
    parse_profiles_document,
)


def profile_dict(profile_id: str = "image-polish", **overrides) -> dict:
    value = {
        "schema_version": 1,
        "id": profile_id,
        "name": "Image Polish",
        "description": "Preserve intent while improving clarity.",
        "system_prompt": "Return only the transformed prompt.",
        "prompt_prefix": "Source: ",
        "prompt_suffix": "\nOutput:",
    }
    value.update(overrides)
    return value


def profile_document(*profiles: dict) -> str:
    return json.dumps({"schema_version": 1, "profiles": list(profiles)})


def test_freeform_is_the_only_built_in_and_has_compact_deterministic_json():
    assert FREEFORM_PROFILE == TaskProfileSnapshot(
        profile_id="freeform",
        name="Freeform",
        description="No prompt transformation.",
    )
    assert FREEFORM_SNAPSHOT_JSON == FREEFORM_PROFILE.to_json()
    assert "\n" not in FREEFORM_SNAPSHOT_JSON
    assert FREEFORM_SNAPSHOT_JSON == json.dumps(
        json.loads(FREEFORM_SNAPSHOT_JSON),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert parse_profiles_document(profile_document()) == (FREEFORM_PROFILE,)


def test_snapshot_is_frozen_and_round_trips_with_stable_content_hash():
    profile = TaskProfileSnapshot.from_dict(profile_dict(name="Polish ✨"))
    restored = TaskProfileSnapshot.from_json(profile.to_json())
    assert restored == profile
    assert restored.to_json() == profile.to_json()
    assert restored.content_sha256 == profile.content_sha256
    assert parse_profile_snapshot(profile.as_dict()) == profile
    with pytest.raises(dataclasses.FrozenInstanceError):
        profile.name = "Changed"


def test_profile_applies_only_prefix_suffix_and_exact_empty_system_fill():
    profile = TaskProfileSnapshot.from_dict(profile_dict())
    prompt, system = apply_task_profile(profile, "a forest", "")
    assert prompt == "Source: a forest\nOutput:"
    assert system == "Return only the transformed prompt."

    prompt, system = apply_task_profile(profile, "a forest", " ")
    assert prompt == "Source: a forest\nOutput:"
    assert system == " "
    assert list(inspect.signature(apply_task_profile).parameters) == [
        "profile",
        "prompt",
        "system_prompt",
    ]


def test_snapshot_requires_exact_keys_and_rejects_duplicate_json_keys():
    value = profile_dict()
    value["sampling"] = {"temperature": 0.1}
    with pytest.raises(ProfileValidationError, match="unexpected"):
        TaskProfileSnapshot.from_dict(value)

    duplicate = (
        '{"schema_version":1,"id":"one","id":"two","name":"Name",'
        '"description":"","system_prompt":"","prompt_prefix":"","prompt_suffix":""}'
    )
    with pytest.raises(ProfileValidationError, match="duplicate JSON key"):
        TaskProfileSnapshot.from_json(duplicate)
    with pytest.raises(ProfileValidationError, match="whitespace-only"):
        TaskProfileSnapshot.from_dict(profile_dict(name="   "))


@pytest.mark.parametrize(
    "encoded",
    (
        FREEFORM_SNAPSHOT_JSON.encode("utf-8-sig"),
        FREEFORM_SNAPSHOT_JSON.encode("utf-16"),
        FREEFORM_SNAPSHOT_JSON.encode("utf-16-le"),
        FREEFORM_SNAPSHOT_JSON.encode("utf-32"),
        FREEFORM_SNAPSHOT_JSON.encode("utf-32-le"),
    ),
)
def test_profile_bytes_accept_only_unmarked_utf8(encoded):
    with pytest.raises(ProfileValidationError):
        TaskProfileSnapshot.from_json(encoded)


def test_profile_text_rejects_a_unicode_byte_order_mark():
    with pytest.raises(ProfileValidationError, match="byte-order mark"):
        TaskProfileSnapshot.from_json(f"\ufeff{FREEFORM_SNAPSHOT_JSON}")


@pytest.mark.parametrize(
    "field_name",
    ("name", "description", "system_prompt", "prompt_prefix", "prompt_suffix"),
)
def test_profile_known_fields_reject_unpaired_unicode_surrogates(field_name):
    value = profile_dict()
    value[field_name] = "\ud800"

    with pytest.raises(ProfileValidationError, match="valid Unicode"):
        TaskProfileSnapshot.from_json(json.dumps(value))


def test_profile_document_bytes_decode_explicitly_as_utf8():
    document = profile_document(profile_dict("portable"))
    assert [item.profile_id for item in parse_profiles_document(document.encode("utf-8"))] == [
        "freeform",
        "portable",
    ]
    with pytest.raises(ProfileValidationError):
        parse_profiles_document(document.encode("utf-16"))


@pytest.mark.parametrize(
    ("field_name", "maximum"),
    (
        ("id", MAX_PROFILE_ID_CHARS),
        ("name", MAX_PROFILE_NAME_CHARS),
        ("description", MAX_PROFILE_DESCRIPTION_CHARS),
        ("system_prompt", MAX_PROFILE_TEXT_CHARS),
        ("prompt_prefix", MAX_PROFILE_TEXT_CHARS),
        ("prompt_suffix", MAX_PROFILE_TEXT_CHARS),
    ),
)
def test_snapshot_enforces_every_text_limit(field_name, maximum):
    value = profile_dict()
    value[field_name] = "x" * (maximum + 1)
    with pytest.raises(ProfileValidationError, match="exceeds"):
        TaskProfileSnapshot.from_dict(value)


@pytest.mark.parametrize(
    "profile_id",
    ("Uppercase", "../escape", "space here", "_starts-with-underscore", ""),
)
def test_profile_id_is_safe_and_portable(profile_id):
    value = profile_dict(profile_id)
    with pytest.raises(ProfileValidationError, match="profile id"):
        TaskProfileSnapshot.from_dict(value)


def test_document_preserves_authored_order_after_freeform():
    profiles = parse_profiles_document(
        profile_document(
            profile_dict("second", name="Second"),
            profile_dict("first", name="First"),
        )
    )
    assert [profile.profile_id for profile in profiles] == ["freeform", "second", "first"]


def test_document_rejects_reserved_duplicate_and_excess_profile_counts():
    with pytest.raises(ProfileValidationError, match="cannot be overridden"):
        parse_profiles_document(profile_document(FREEFORM_PROFILE.as_dict()))

    with pytest.raises(ProfileValidationError, match="duplicate profile id"):
        parse_profiles_document(profile_document(profile_dict("same"), profile_dict("same")))

    profiles = [profile_dict(f"profile-{index}") for index in range(MAX_PROFILE_COUNT + 1)]
    with pytest.raises(ProfileValidationError, match=f"exceeds {MAX_PROFILE_COUNT}"):
        parse_profiles_document(profile_document(*profiles))


def test_document_rejects_unknown_root_keys_and_nonfinite_json():
    with pytest.raises(ProfileValidationError, match="unexpected"):
        parse_profiles_document(
            json.dumps({"schema_version": 1, "profiles": [], "include": "other.json"})
        )
    with pytest.raises(ProfileValidationError, match="invalid JSON constant"):
        parse_profiles_document('{"schema_version":1,"profiles":[NaN]}')


def test_loader_accepts_only_a_caller_resolved_path_and_missing_means_freeform(tmp_path):
    arbitrary_user_path = tmp_path / "tenant-a" / "custom" / "profiles.json"
    assert load_profiles(arbitrary_user_path) == (FREEFORM_PROFILE,)

    arbitrary_user_path.parent.mkdir(parents=True)
    arbitrary_user_path.write_text(
        profile_document(profile_dict("local-profile")),
        encoding="utf-8",
    )
    loaded = load_profiles(arbitrary_user_path)
    assert [profile.profile_id for profile in loaded] == ["freeform", "local-profile"]


def test_loader_stops_at_the_file_byte_limit(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_bytes(b"x" * (MAX_PROFILE_FILE_BYTES + 1))
    with pytest.raises(ProfileValidationError, match=f"exceeds {MAX_PROFILE_FILE_BYTES} bytes"):
        load_profiles(path)


def test_profile_node_has_exactly_one_serialized_primitive_and_uses_saved_snapshot(
    node_package,
):
    module = importlib.import_module(f"{node_package.__name__}.nodes.task_profile")
    node_class = module.LlamaCppTaskProfile
    schema = node_class.INPUT_TYPES()

    assert list(schema) == ["required"]
    assert list(schema["required"]) == ["profile_snapshot"]
    declared_type, options = schema["required"]["profile_snapshot"]
    assert declared_type == "STRING"
    assert options["default"] == module.FREEFORM_SNAPSHOT_JSON
    assert options["hidden"] is True
    assert node_class.RETURN_TYPES == ("LLAMACPP_PROFILE",)
    assert node_class.RETURN_NAMES == ("profile",)

    saved = module.parse_profile_snapshot(profile_dict("portable"))
    output = node_class().create_profile(saved.to_json())
    assert output == (saved,)


def test_profile_node_never_looks_up_a_mutable_local_name(node_package, monkeypatch):
    module = importlib.import_module(f"{node_package.__name__}.nodes.task_profile")
    saved = module.parse_profile_snapshot(profile_dict("portable"))
    monkeypatch.setattr(
        module,
        "parse_profile_snapshot",
        lambda value: saved if value == saved.to_json() else None,
    )
    assert module.LlamaCppTaskProfile().create_profile(saved.to_json()) == (saved,)
