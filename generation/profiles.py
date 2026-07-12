"""Bounded, deterministic task-profile snapshots for canonical generation."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

PROFILE_SCHEMA_VERSION = 1
MAX_PROFILE_FILE_BYTES = 1_048_576
MAX_PROFILE_SNAPSHOT_BYTES = 262_144
MAX_PROFILE_COUNT = 128
MAX_PROFILE_ID_CHARS = 64
MAX_PROFILE_NAME_CHARS = 128
MAX_PROFILE_DESCRIPTION_CHARS = 2_048
MAX_PROFILE_TEXT_CHARS = 65_536

_PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_PROFILE_KEYS = frozenset(
    {
        "schema_version",
        "id",
        "name",
        "description",
        "system_prompt",
        "prompt_prefix",
        "prompt_suffix",
    }
)
_DOCUMENT_KEYS = frozenset({"schema_version", "profiles"})


class ProfileValidationError(ValueError):
    """Raised when untrusted local or workflow profile data is invalid."""


def _compact_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _exact_keys(value: Mapping[str, Any], expected: frozenset[str], name: str) -> None:
    actual = set(value)
    if actual == set(expected):
        return
    missing = sorted(set(expected) - actual, key=repr)
    extra = sorted(actual - set(expected), key=repr)
    details = []
    if missing:
        details.append(f"missing {missing}")
    if extra:
        details.append(f"unexpected {extra}")
    raise ProfileValidationError(f"{name} has invalid keys ({'; '.join(details)})")


def _text(
    value: Any,
    field_name: str,
    maximum: int,
    *,
    allow_empty: bool = True,
) -> str:
    if not isinstance(value, str):
        raise ProfileValidationError(f"{field_name} must be a string")
    if not allow_empty and not value:
        raise ProfileValidationError(f"{field_name} must not be empty")
    if len(value) > maximum:
        raise ProfileValidationError(f"{field_name} exceeds {maximum} characters")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise ProfileValidationError(f"{field_name} must contain valid Unicode text") from None
    return value


def _schema_version(value: Any, field_name: str) -> int:
    if type(value) is not int or value != PROFILE_SCHEMA_VERSION:
        raise ProfileValidationError(f"{field_name} must be integer {PROFILE_SCHEMA_VERSION}")
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProfileValidationError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ProfileValidationError(f"invalid JSON constant: {value}")


def _parse_json(value: str | bytes, name: str, *, maximum_bytes: int) -> Any:
    if isinstance(value, str):
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ProfileValidationError(f"{name} must contain valid Unicode text") from exc
    elif isinstance(value, bytes):
        encoded = value
    else:
        raise ProfileValidationError(f"{name} must be UTF-8 JSON text")
    if len(encoded) > maximum_bytes:
        raise ProfileValidationError(f"{name} exceeds {maximum_bytes} bytes")
    try:
        text = encoded.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProfileValidationError(f"{name} must use UTF-8 encoding") from exc
    if text.startswith("\ufeff"):
        raise ProfileValidationError(f"{name} must use UTF-8 without a byte-order mark")
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise ProfileValidationError(f"{name} is not valid JSON: {exc}") from exc


@dataclass(frozen=True, slots=True)
class TaskProfileSnapshot:
    profile_id: str
    name: str
    description: str = ""
    system_prompt: str = ""
    prompt_prefix: str = ""
    prompt_suffix: str = ""

    SCHEMA_VERSION: ClassVar[int] = PROFILE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        profile_id = _text(
            self.profile_id,
            "profile id",
            MAX_PROFILE_ID_CHARS,
            allow_empty=False,
        )
        if not _PROFILE_ID_RE.fullmatch(profile_id):
            raise ProfileValidationError(
                "profile id must use lowercase letters, digits, dots, underscores, or hyphens"
            )
        object.__setattr__(self, "profile_id", profile_id)
        name = _text(self.name, "profile name", MAX_PROFILE_NAME_CHARS, allow_empty=False)
        if not name.strip():
            raise ProfileValidationError("profile name must not be whitespace-only")
        object.__setattr__(self, "name", name)
        object.__setattr__(
            self,
            "description",
            _text(self.description, "profile description", MAX_PROFILE_DESCRIPTION_CHARS),
        )
        for field_name in ("system_prompt", "prompt_prefix", "prompt_suffix"):
            object.__setattr__(
                self,
                field_name,
                _text(
                    getattr(self, field_name),
                    field_name.replace("_", " "),
                    MAX_PROFILE_TEXT_CHARS,
                ),
            )
        encoded = self.to_json().encode("utf-8")
        if len(encoded) > MAX_PROFILE_SNAPSHOT_BYTES:
            raise ProfileValidationError(
                f"profile snapshot exceeds {MAX_PROFILE_SNAPSHOT_BYTES} bytes"
            )

    @property
    def id(self) -> str:
        """The stable serialized profile identifier."""

        return self.profile_id

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "id": self.profile_id,
            "name": self.name,
            "description": self.description,
            "system_prompt": self.system_prompt,
            "prompt_prefix": self.prompt_prefix,
            "prompt_suffix": self.prompt_suffix,
        }

    def to_json(self) -> str:
        return _compact_json(self.as_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TaskProfileSnapshot:
        if not isinstance(value, Mapping):
            raise ProfileValidationError("profile snapshot must be an object")
        _exact_keys(value, _PROFILE_KEYS, "profile snapshot")
        _schema_version(value["schema_version"], "profile snapshot schema_version")
        return cls(
            profile_id=value["id"],
            name=value["name"],
            description=value["description"],
            system_prompt=value["system_prompt"],
            prompt_prefix=value["prompt_prefix"],
            prompt_suffix=value["prompt_suffix"],
        )

    @classmethod
    def from_json(cls, value: str | bytes) -> TaskProfileSnapshot:
        parsed = _parse_json(
            value,
            "profile snapshot",
            maximum_bytes=MAX_PROFILE_SNAPSHOT_BYTES,
        )
        if not isinstance(parsed, Mapping):
            raise ProfileValidationError("profile snapshot must contain a JSON object")
        return cls.from_dict(parsed)


FREEFORM_PROFILE = TaskProfileSnapshot(
    profile_id="freeform",
    name="Freeform",
    description="No prompt transformation.",
)
FREEFORM_SNAPSHOT_JSON = FREEFORM_PROFILE.to_json()


def parse_profile_snapshot(value: str | bytes | Mapping[str, Any]) -> TaskProfileSnapshot:
    """Parse one saved workflow snapshot without consulting mutable local storage."""

    if isinstance(value, Mapping):
        return TaskProfileSnapshot.from_dict(value)
    return TaskProfileSnapshot.from_json(value)


def parse_profiles_document(value: str | bytes) -> tuple[TaskProfileSnapshot, ...]:
    """Parse a complete bounded user profile document, including Freeform."""

    parsed = _parse_json(value, "profile document", maximum_bytes=MAX_PROFILE_FILE_BYTES)
    if not isinstance(parsed, Mapping):
        raise ProfileValidationError("profile document must contain a JSON object")
    _exact_keys(parsed, _DOCUMENT_KEYS, "profile document")
    _schema_version(parsed["schema_version"], "profile document schema_version")
    entries = parsed["profiles"]
    if isinstance(entries, (str, bytes)) or not isinstance(entries, Sequence):
        raise ProfileValidationError("profile document profiles must be an array")
    if len(entries) > MAX_PROFILE_COUNT:
        raise ProfileValidationError(f"profile document exceeds {MAX_PROFILE_COUNT} profiles")

    profiles = [FREEFORM_PROFILE]
    seen = {FREEFORM_PROFILE.profile_id}
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            raise ProfileValidationError(f"profile document entry {index} must be an object")
        profile = TaskProfileSnapshot.from_dict(entry)
        if profile.profile_id in seen:
            if profile.profile_id == FREEFORM_PROFILE.profile_id:
                raise ProfileValidationError("the built-in freeform profile cannot be overridden")
            raise ProfileValidationError(f"duplicate profile id: {profile.profile_id!r}")
        seen.add(profile.profile_id)
        profiles.append(profile)
    return tuple(profiles)


def load_profiles(path: str | Path) -> tuple[TaskProfileSnapshot, ...]:
    """Load a caller-resolved profile path without assuming a Comfy user.

    A missing file means the user has no local profiles. Other filesystem errors
    are surfaced so an HTTP layer can report them without silently discarding an
    invalid or unreadable user configuration.
    """

    profile_path = Path(path)
    try:
        with profile_path.open("rb") as handle:
            data = handle.read(MAX_PROFILE_FILE_BYTES + 1)
    except FileNotFoundError:
        return (FREEFORM_PROFILE,)
    except OSError as exc:
        raise ProfileValidationError(
            f"could not read profile document ({type(exc).__name__})"
        ) from exc
    if len(data) > MAX_PROFILE_FILE_BYTES:
        raise ProfileValidationError(f"profile document exceeds {MAX_PROFILE_FILE_BYTES} bytes")
    return parse_profiles_document(data)


def apply_task_profile(
    profile: TaskProfileSnapshot,
    prompt: str,
    system_prompt: str,
) -> tuple[str, str]:
    """Apply only the profile's explicit text transform.

    The caller owns every generation control. A profile can wrap prompt text and
    fill an exactly empty system prompt, but it cannot inspect or replace any
    other request setting.
    """

    if not isinstance(profile, TaskProfileSnapshot):
        raise TypeError("profile must be TaskProfileSnapshot")
    if not isinstance(prompt, str) or not isinstance(system_prompt, str):
        raise TypeError("prompt and system_prompt must be strings")
    effective_prompt = f"{profile.prompt_prefix}{prompt}{profile.prompt_suffix}"
    effective_system = profile.system_prompt if system_prompt == "" else system_prompt
    return effective_prompt, effective_system


__all__ = [
    "FREEFORM_PROFILE",
    "FREEFORM_SNAPSHOT_JSON",
    "MAX_PROFILE_COUNT",
    "MAX_PROFILE_DESCRIPTION_CHARS",
    "MAX_PROFILE_FILE_BYTES",
    "MAX_PROFILE_ID_CHARS",
    "MAX_PROFILE_NAME_CHARS",
    "MAX_PROFILE_SNAPSHOT_BYTES",
    "MAX_PROFILE_TEXT_CHARS",
    "PROFILE_SCHEMA_VERSION",
    "ProfileValidationError",
    "TaskProfileSnapshot",
    "apply_task_profile",
    "load_profiles",
    "parse_profile_snapshot",
    "parse_profiles_document",
]
