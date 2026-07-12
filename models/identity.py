"""Exact and ambiguity-safe router model identity resolution."""

from __future__ import annotations

import ntpath
import posixpath
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

_MAX_ROUTER_ARGS = 4096
_MAX_ROUTER_ARG_CHARS = 32_768
_MAX_ROUTER_ARGS_TOTAL_CHARS = 262_144
_MAX_ROUTER_PRESET_CHARS = 65_536

_TargetIdentity = tuple[str, str]


class RouterIdentityError(ValueError):
    pass


class _RouterTargetEvidenceError(ValueError):
    pass


def _record_names(record: Mapping[str, Any]) -> set[str]:
    names: set[str] = set()
    for key in ("id", "model", "name"):
        value = record.get(key)
        if isinstance(value, str) and value:
            names.add(value)
    aliases = record.get("aliases") or record.get("alias") or []
    if isinstance(aliases, str):
        aliases = [aliases]
    if isinstance(aliases, Iterable):
        names.update(alias for alias in aliases if isinstance(alias, str) and alias)
    return names


def _candidates(model_name: str) -> list[str]:
    normalized = model_name.strip().replace("\\", "/")
    if not normalized:
        raise RouterIdentityError("Model name cannot be empty")
    no_extension = normalized[:-5] if normalized.lower().endswith(".gguf") else normalized
    candidates = [normalized, no_extension]
    parts = no_extension.split("/")
    if len(parts) > 1:
        candidates.extend((parts[0], parts[-1]))
    return list(dict.fromkeys(candidates))


def _local_gguf_path(model_name: str) -> str | None:
    normalized = model_name.strip().replace("\\", "/")
    if not normalized.lower().endswith(".gguf"):
        return None
    parts = normalized.split("/")
    if (
        "\x00" in normalized
        or not parts
        or any(part in {"", ".", ".."} for part in parts)
        or (len(parts[0]) >= 2 and parts[0][1] == ":")
    ):
        raise RouterIdentityError("Local GGUF selection must use a normalized relative model path")
    return "/".join(parts)


def _normalize_target(value: str) -> _TargetIdentity:
    if "\x00" in value:
        raise _RouterTargetEvidenceError
    windows = (
        "\\" in value
        or value.startswith("//")
        or (len(value) >= 2 and value[0].isalpha() and value[1] == ":")
    )
    if windows:
        return "windows", ntpath.normcase(ntpath.normpath(value))
    return "posix", posixpath.normpath(value)


def _unique_target(values: Iterable[str]) -> _TargetIdentity | None:
    targets = {_normalize_target(value) for value in values if value}
    if len(targets) > 1:
        raise _RouterTargetEvidenceError
    return next(iter(targets), None)


def _target_from_args(value: object) -> _TargetIdentity | None:
    if value is None:
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _RouterTargetEvidenceError
    if len(value) > _MAX_ROUTER_ARGS:
        raise _RouterTargetEvidenceError

    args: list[str] = []
    total_chars = 0
    for item in value:
        if not isinstance(item, str) or "\x00" in item or len(item) > _MAX_ROUTER_ARG_CHARS:
            raise _RouterTargetEvidenceError
        total_chars += len(item)
        if total_chars > _MAX_ROUTER_ARGS_TOTAL_CHARS:
            raise _RouterTargetEvidenceError
        args.append(item)

    targets: list[str] = []
    index = 0
    while index < len(args):
        argument = args[index]
        if argument in {"--model", "-m"}:
            index += 1
            if index >= len(args) or not args[index]:
                raise _RouterTargetEvidenceError
            targets.append(args[index])
        elif argument.startswith("--model=") or argument.startswith("-m="):
            target = argument.split("=", 1)[1]
            if not target:
                raise _RouterTargetEvidenceError
            targets.append(target)
        index += 1
    return _unique_target(targets)


def _target_from_preset(value: object) -> _TargetIdentity | None:
    if value is None:
        return None
    if not isinstance(value, str) or "\x00" in value or len(value) > _MAX_ROUTER_PRESET_CHARS:
        raise _RouterTargetEvidenceError

    targets: list[str] = []
    for raw_line in value.splitlines():
        key, separator, target = raw_line.partition("=")
        if separator and key.strip().casefold() == "model":
            target = target.strip()
            if not target:
                raise _RouterTargetEvidenceError
            targets.append(target)
    return _unique_target(targets)


def _record_target(record: Mapping[str, Any]) -> _TargetIdentity | None:
    status = record.get("status")
    if not isinstance(status, Mapping):
        return None
    args_target = _target_from_args(status.get("args"))
    preset_target = _target_from_preset(status.get("preset"))
    if args_target is not None and preset_target is not None and args_target != preset_target:
        raise _RouterTargetEvidenceError
    return args_target or preset_target


def _validate_local_target(
    model_name: str,
    canonical: str,
    records: Iterable[Mapping[str, Any]],
    *,
    expected_local_path: str | None,
    allow_exact_server_identity: bool,
) -> None:
    requested = _local_gguf_path(model_name)
    if requested is None:
        return
    record_list = tuple(records)
    try:
        targets = {
            target for record in record_list if (target := _record_target(record)) is not None
        }
        if len(targets) > 1:
            raise _RouterTargetEvidenceError
        target = next(iter(targets), None)
    except _RouterTargetEvidenceError:
        raise RouterIdentityError(
            f"Router model {canonical!r} returned inconsistent target metadata; "
            "refusing exact local model resolution"
        ) from None
    if target is None:
        return
    if expected_local_path is None:
        exact_server_identity = allow_exact_server_identity and model_name == canonical
        if exact_server_identity:
            return
        raise RouterIdentityError(
            f"Router model {canonical!r} target metadata cannot be anchored to the "
            "active router root; select an exact live router ID or a local GGUF under "
            "the active root"
        )
    try:
        expected = _normalize_target(expected_local_path)
    except _RouterTargetEvidenceError:
        raise RouterIdentityError(
            f"Router model {canonical!r} local target is invalid; "
            "refusing exact local model resolution"
        ) from None
    if target != expected:
        raise RouterIdentityError(
            f"Router model {canonical!r} targets a different GGUF than the selected "
            "local model; keep one base GGUF per router directory or define distinct presets"
        )


def resolve_router_model(
    model_name: str,
    server_models: Iterable[Mapping[str, Any]],
    *,
    expected_local_path: str | None = None,
    allow_exact_server_identity: bool = False,
) -> str:
    """Resolve one model to an exact server ID, anchoring local GGUFs when possible."""

    records: list[tuple[str, set[str], Mapping[str, Any]]] = []
    for record in server_models:
        if not isinstance(record, Mapping):
            continue
        names = _record_names(record)
        canonical = record.get("id") or record.get("model") or record.get("name")
        if isinstance(canonical, str) and canonical:
            records.append((canonical, names, record))
    if not records:
        raise RouterIdentityError("Router returned no usable model identities")

    candidates = _candidates(model_name)
    _local_gguf_path(model_name)
    exact = {canonical for canonical, names, _record in records if names.intersection(candidates)}
    if len(exact) == 1:
        canonical = exact.pop()
        _validate_local_target(
            model_name,
            canonical,
            (record for identity, _names, record in records if identity == canonical),
            expected_local_path=expected_local_path,
            allow_exact_server_identity=allow_exact_server_identity,
        )
        return canonical
    if len(exact) > 1:
        raise RouterIdentityError(
            f"Model {model_name!r} is ambiguous across router IDs: {sorted(exact)}"
        )

    folded = {candidate.casefold() for candidate in candidates}
    insensitive = {
        canonical
        for canonical, names, _record in records
        if any(name.casefold() in folded for name in names)
    }
    if len(insensitive) == 1:
        canonical = insensitive.pop()
        _validate_local_target(
            model_name,
            canonical,
            (record for identity, _names, record in records if identity == canonical),
            expected_local_path=expected_local_path,
            allow_exact_server_identity=allow_exact_server_identity,
        )
        return canonical
    if len(insensitive) > 1:
        raise RouterIdentityError(
            f"Model {model_name!r} is ambiguous across router IDs: {sorted(insensitive)}"
        )
    available = sorted(canonical for canonical, _names, _record in records)
    raise RouterIdentityError(
        f"Model {model_name!r} was not found in router identities: {available}"
    )
