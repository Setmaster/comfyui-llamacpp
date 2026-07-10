"""Exact and ambiguity-safe router model identity resolution."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


class RouterIdentityError(ValueError):
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


def resolve_router_model(model_name: str, server_models: Iterable[Mapping[str, Any]]) -> str:
    """Resolve a local catalog name to one exact server ID or fail visibly."""

    records: list[tuple[str, set[str]]] = []
    for record in server_models:
        if not isinstance(record, Mapping):
            continue
        names = _record_names(record)
        canonical = record.get("id") or record.get("model") or record.get("name")
        if isinstance(canonical, str) and canonical:
            records.append((canonical, names))
    if not records:
        raise RouterIdentityError("Router returned no usable model identities")

    candidates = _candidates(model_name)
    exact = {canonical for canonical, names in records if names.intersection(candidates)}
    if len(exact) == 1:
        return exact.pop()
    if len(exact) > 1:
        raise RouterIdentityError(
            f"Model {model_name!r} is ambiguous across router IDs: {sorted(exact)}"
        )

    folded = {candidate.casefold() for candidate in candidates}
    insensitive = {
        canonical
        for canonical, names in records
        if any(name.casefold() in folded for name in names)
    }
    if len(insensitive) == 1:
        return insensitive.pop()
    if len(insensitive) > 1:
        raise RouterIdentityError(
            f"Model {model_name!r} is ambiguous across router IDs: {sorted(insensitive)}"
        )
    available = sorted(canonical for canonical, _ in records)
    raise RouterIdentityError(
        f"Model {model_name!r} was not found in router identities: {available}"
    )
