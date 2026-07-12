"""Passive, provenance-preserving runtime model discovery."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Generic, TypeVar

if "." in __package__:
    from ..models.catalog import ModelCatalog, ModelCatalogError
else:  # standalone runtime tests
    from models.catalog import ModelCatalog, ModelCatalogError
from .client import LlamaClientError, ModelState, RouterModel, ServerProps
from .service import RuntimeMode

T = TypeVar("T")
_CAPABILITIES = ("text", "image", "audio", "video")
_PASSIVE_UNAVAILABLE_CODES = frozenset({400, 404, 409, 503})
MAX_DISCOVERY_MODELS = 4096
MAX_DISCOVERY_ALIASES = 128
MAX_DISCOVERY_TEXT_CHARS = 4096
MAX_DISCOVERY_TOTAL_TEXT_BYTES = 8 * 1024 * 1024
_RUNNING_MODEL_SENTINEL = "(use running model)"


def _enum_value(enum_type: type[Enum], value: Any, field_name: str) -> Enum:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} is invalid") from exc


def _bounded_text(value: Any, field_name: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or (not optional and not value):
        suffix = " or null" if optional else ""
        raise ValueError(f"{field_name} must be a non-empty string{suffix}")
    if len(value) > MAX_DISCOVERY_TEXT_CHARS or len(value.encode("utf-8")) > (
        MAX_DISCOVERY_TEXT_CHARS
    ):
        raise ValueError(f"{field_name} exceeds its bounded size")
    return value


class KnowledgeState(str, Enum):
    KNOWN = "known"
    UNKNOWN = "unknown"


class DiscoverySource(str, Enum):
    PROPS = "props"
    ROUTER = "router"
    LAUNCH_CONFIG = "launch_config"
    LOCAL_CATALOG = "local_catalog"


class SavedModelState(str, Enum):
    UNSPECIFIED = "unspecified"
    AVAILABLE = "available"
    MISSING = "missing"


class RuntimeOwnership(str, Enum):
    OWNED = "owned"
    ATTACHED = "attached"
    OFFLINE = "offline"


@dataclass(frozen=True, slots=True)
class Fact(Generic[T]):
    state: KnowledgeState
    value: T | None = None
    source: DiscoverySource | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", _enum_value(KnowledgeState, self.state, "fact state"))
        if self.source is not None:
            object.__setattr__(
                self,
                "source",
                _enum_value(DiscoverySource, self.source, "fact source"),
            )
        if self.state == KnowledgeState.KNOWN:
            if self.value is None or self.source is None:
                raise ValueError("known facts require a value and source")
        elif self.value is not None or self.source is not None:
            raise ValueError("unknown facts cannot carry a value or source")

    @classmethod
    def known(cls, value: T, source: DiscoverySource) -> Fact[T]:
        return cls(KnowledgeState.KNOWN, value, source)

    @classmethod
    def unknown(cls) -> Fact[T]:
        return cls(KnowledgeState.UNKNOWN)

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "value": self.value,
            "source": self.source.value if self.source else None,
        }


@dataclass(frozen=True, slots=True)
class ProjectorSuggestion:
    model_name: str
    projector_name: str
    compatibility: Fact[bool]
    requires_confirmation: bool
    evidence: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "model_name", _bounded_text(self.model_name, "model_name"))
        object.__setattr__(
            self,
            "projector_name",
            _bounded_text(self.projector_name, "projector_name"),
        )
        if not isinstance(self.compatibility, Fact):
            raise TypeError("projector compatibility must be a Fact")
        if type(self.requires_confirmation) is not bool:
            raise TypeError("projector requires_confirmation must be a Boolean")
        object.__setattr__(self, "evidence", _bounded_text(self.evidence, "evidence"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_name": self.model_name,
            "projector_name": self.projector_name,
            "compatibility": self.compatibility.as_dict(),
            "requires_confirmation": self.requires_confirmation,
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class RuntimeEndpointSnapshot:
    mode: RuntimeMode
    owned: bool
    runtime_epoch: int | None
    endpoint: str
    active_router_root: str | None = None
    configured_context: int | None = None
    configured_model_path: str | None = None
    configured_projector_path: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", _enum_value(RuntimeMode, self.mode, "runtime mode"))
        if type(self.owned) is not bool:
            raise TypeError("owned must be a Boolean")
        if self.runtime_epoch is not None and (
            type(self.runtime_epoch) is not int or self.runtime_epoch < 0
        ):
            raise ValueError("runtime_epoch must be a non-negative integer or null")
        object.__setattr__(
            self,
            "endpoint",
            "" if self.endpoint == "" else _bounded_text(self.endpoint, "endpoint"),
        )
        for field_name in (
            "active_router_root",
            "configured_model_path",
            "configured_projector_path",
        ):
            object.__setattr__(
                self,
                field_name,
                _bounded_text(getattr(self, field_name), field_name, optional=True),
            )
        if self.configured_context is not None and (
            type(self.configured_context) is not int or self.configured_context <= 0
        ):
            raise ValueError("configured_context must be a positive integer or null")


@dataclass(frozen=True, slots=True)
class RuntimeModelDescriptor:
    model_id: str
    aliases: tuple[str, ...]
    residency: ModelState
    availability: SavedModelState
    input_capabilities: Mapping[str, Fact[bool]]
    context_length: Fact[int]
    model_path: str | None
    ownership: RuntimeOwnership
    release_scope: str | None
    projector: ProjectorSuggestion | None
    source: DiscoverySource

    def __post_init__(self) -> None:
        object.__setattr__(self, "model_id", _bounded_text(self.model_id, "model_id"))
        if len(self.aliases) > MAX_DISCOVERY_ALIASES or any(
            not isinstance(alias, str)
            or not alias
            or len(alias) > MAX_DISCOVERY_TEXT_CHARS
            or len(alias.encode("utf-8")) > MAX_DISCOVERY_TEXT_CHARS
            for alias in self.aliases
        ):
            raise ValueError("discovered model aliases exceed their bounded size")
        object.__setattr__(self, "residency", _enum_value(ModelState, self.residency, "residency"))
        object.__setattr__(
            self,
            "availability",
            _enum_value(SavedModelState, self.availability, "availability"),
        )
        object.__setattr__(
            self,
            "ownership",
            _enum_value(RuntimeOwnership, self.ownership, "ownership"),
        )
        object.__setattr__(
            self,
            "release_scope",
            _bounded_text(self.release_scope, "release_scope", optional=True),
        )
        object.__setattr__(
            self,
            "model_path",
            _bounded_text(self.model_path, "model_path", optional=True),
        )
        object.__setattr__(self, "source", _enum_value(DiscoverySource, self.source, "source"))
        if self.projector is not None and not isinstance(self.projector, ProjectorSuggestion):
            raise TypeError("projector must be a ProjectorSuggestion or null")
        if not isinstance(self.context_length, Fact):
            raise TypeError("context_length must be a Fact")
        normalized = {
            name: self.input_capabilities.get(name, Fact.unknown()) for name in _CAPABILITIES
        }
        if any(not isinstance(fact, Fact) for fact in normalized.values()):
            raise TypeError("input capabilities must contain Fact values")
        object.__setattr__(self, "input_capabilities", MappingProxyType(normalized))

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "aliases": list(self.aliases),
            "residency": self.residency.value,
            "availability": self.availability.value,
            "input_capabilities": {
                name: fact.as_dict() for name, fact in self.input_capabilities.items()
            },
            "context_length": self.context_length.as_dict(),
            "model_path": self.model_path,
            "ownership": self.ownership.value,
            "release_scope": self.release_scope,
            "projector": self.projector.as_dict() if self.projector else None,
            "source": self.source.value,
        }


@dataclass(frozen=True, slots=True)
class RuntimeDiscoverySnapshot:
    mode: RuntimeMode
    owned: bool
    runtime_epoch: int | None
    endpoint: str
    active_router_root: str | None
    models: tuple[RuntimeModelDescriptor, ...]
    saved_model: str | None
    saved_model_state: SavedModelState
    captured_at: float
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", _enum_value(RuntimeMode, self.mode, "runtime mode"))
        if type(self.owned) is not bool:
            raise TypeError("owned must be a Boolean")
        if self.runtime_epoch is not None and (
            type(self.runtime_epoch) is not int or self.runtime_epoch < 0
        ):
            raise ValueError("runtime_epoch must be a non-negative integer or null")
        object.__setattr__(
            self,
            "endpoint",
            "" if self.endpoint == "" else _bounded_text(self.endpoint, "endpoint"),
        )
        object.__setattr__(
            self,
            "active_router_root",
            _bounded_text(self.active_router_root, "active_router_root", optional=True),
        )
        object.__setattr__(
            self,
            "saved_model",
            _bounded_text(self.saved_model, "saved_model", optional=True),
        )
        object.__setattr__(
            self,
            "saved_model_state",
            _enum_value(SavedModelState, self.saved_model_state, "saved_model_state"),
        )
        if not isinstance(self.captured_at, (int, float)) or isinstance(self.captured_at, bool):
            raise TypeError("captured_at must be a number")
        if not math.isfinite(float(self.captured_at)) or self.captured_at < 0:
            raise ValueError("captured_at must be finite and non-negative")
        if len(self.models) > MAX_DISCOVERY_MODELS:
            raise ValueError(f"runtime discovery exceeds {MAX_DISCOVERY_MODELS} models")
        if any(not isinstance(model, RuntimeModelDescriptor) for model in self.models):
            raise TypeError("runtime discovery models must be RuntimeModelDescriptor values")
        if len(self.warnings) > 64 or any(
            not isinstance(warning, str)
            or not warning
            or len(warning) > MAX_DISCOVERY_TEXT_CHARS
            or len(warning.encode("utf-8")) > MAX_DISCOVERY_TEXT_CHARS
            for warning in self.warnings
        ):
            raise ValueError("runtime discovery warnings exceed their bounded size")
        total_text_bytes = sum(
            len(value.encode("utf-8"))
            for value in (
                self.endpoint,
                self.active_router_root or "",
                self.saved_model or "",
                *self.warnings,
                *(
                    value
                    for model in self.models
                    for value in (
                        model.model_id,
                        *model.aliases,
                        model.model_path or "",
                        model.release_scope or "",
                        model.projector.model_name if model.projector else "",
                        model.projector.projector_name if model.projector else "",
                        model.projector.evidence if model.projector else "",
                    )
                ),
            )
        )
        if total_text_bytes > MAX_DISCOVERY_TOTAL_TEXT_BYTES:
            raise ValueError("runtime discovery exceeds its total text budget")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "mode": self.mode.value,
            "owned": self.owned,
            "runtime_epoch": self.runtime_epoch,
            "endpoint": self.endpoint,
            "active_router_root": self.active_router_root,
            "models": [model.as_dict() for model in self.models],
            "saved_model": self.saved_model,
            "saved_model_state": self.saved_model_state.value,
            "captured_at": self.captured_at,
            "warnings": list(self.warnings),
        }

    def public_dict(self) -> dict[str, Any]:
        """Return only browser-required passive facts, without local absolute paths."""

        payload = self.as_dict()
        payload.pop("endpoint", None)
        payload.pop("active_router_root", None)
        for model in payload["models"]:
            model.pop("model_path", None)
        return payload


def _unknown_capabilities() -> dict[str, Fact[bool]]:
    return {name: Fact.unknown() for name in _CAPABILITIES}


def _router_capabilities(model: RouterModel) -> dict[str, Fact[bool]]:
    capabilities = _unknown_capabilities()
    for upstream_name in model.input_modalities:
        name = "image" if upstream_name == "vision" else upstream_name
        if name in capabilities:
            capabilities[name] = Fact.known(True, DiscoverySource.ROUTER)
    return capabilities


def _apply_props(
    capabilities: dict[str, Fact[bool]],
    props: ServerProps,
) -> None:
    capabilities["text"] = Fact.known(True, DiscoverySource.PROPS)
    for upstream_name, supported in props.modalities.items():
        name = "image" if upstream_name == "vision" else upstream_name
        if name in capabilities:
            capabilities[name] = Fact.known(bool(supported), DiscoverySource.PROPS)


def _context_from_props(props: ServerProps) -> Fact[int]:
    generation = props.raw.get("default_generation_settings")
    if not isinstance(generation, Mapping):
        return Fact.unknown()
    value = generation.get("n_ctx")
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        params = generation.get("params")
        value = params.get("n_ctx") if isinstance(params, Mapping) else None
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return Fact.known(value, DiscoverySource.PROPS)
    return Fact.unknown()


def _configured_context(snapshot: RuntimeEndpointSnapshot) -> Fact[int]:
    value = snapshot.configured_context
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return Fact.known(value, DiscoverySource.LAUNCH_CONFIG)
    return Fact.unknown()


def _matches_saved(model: RouterModel, saved_model: str) -> bool:
    return saved_model == model.id or saved_model in model.aliases


def _projector_suggestion(
    catalog: ModelCatalog | None,
    model_name: str,
) -> ProjectorSuggestion | None:
    if catalog is None or not model_name:
        return None
    try:
        projector = catalog.adjacent_projector(model_name)
    except (ModelCatalogError, OSError):
        return None
    if projector is None:
        return None
    return ProjectorSuggestion(
        model_name=model_name,
        projector_name=projector.name,
        compatibility=Fact.unknown(),
        requires_confirmation=True,
        evidence="unique_adjacent_file",
    )


def _catalog_model_identity(
    catalog: ModelCatalog | None,
    *candidates: str | None,
) -> str:
    if catalog is None:
        return ""
    try:
        entries = tuple(entry for entry in catalog.entries() if not entry.is_mmproj)
    except OSError:
        return ""
    for candidate in candidates:
        if not candidate:
            continue
        try:
            resolved = Path(candidate).expanduser().resolve()
        except (OSError, ValueError):
            resolved = None
        if resolved is not None:
            matches = [entry for entry in entries if entry.path == resolved]
            if len(matches) == 1:
                return matches[0].name
        normalized = candidate.replace("\\", "/")
        matches = [entry for entry in entries if entry.name == normalized]
        if len(matches) == 1:
            return matches[0].name
    return ""


def _offline_projector_suggestions(entries: tuple[Any, ...]) -> dict[str, ProjectorSuggestion]:
    projectors: dict[tuple[Path, Path], list[Any]] = {}
    for entry in entries:
        if entry.is_mmproj:
            projectors.setdefault((entry.root, entry.path.parent), []).append(entry)

    suggestions: dict[str, ProjectorSuggestion] = {}
    for entry in entries:
        if entry.is_mmproj:
            continue
        adjacent = projectors.get((entry.root, entry.path.parent), ())
        if len(adjacent) != 1:
            continue
        suggestions[entry.name] = ProjectorSuggestion(
            model_name=entry.name,
            projector_name=adjacent[0].name,
            compatibility=Fact.unknown(),
            requires_confirmation=True,
            evidence="unique_adjacent_file",
        )
    return suggestions


def _offline_discovery(
    snapshot: RuntimeEndpointSnapshot,
    saved_model: str,
    *,
    catalog: ModelCatalog,
    captured_at: float,
    warning: str | None = None,
) -> RuntimeDiscoverySnapshot:
    models: list[RuntimeModelDescriptor] = []
    all_entries = tuple(catalog.entries())
    entries = tuple(entry for entry in all_entries if not entry.is_mmproj)
    projector_suggestions = _offline_projector_suggestions(all_entries)
    truncated = len(entries) > MAX_DISCOVERY_MODELS
    for entry in entries[:MAX_DISCOVERY_MODELS]:
        models.append(
            RuntimeModelDescriptor(
                model_id=entry.name,
                aliases=(),
                residency=ModelState.UNKNOWN,
                availability=SavedModelState.AVAILABLE,
                input_capabilities=_unknown_capabilities(),
                context_length=Fact.unknown(),
                model_path=str(entry.path),
                ownership=RuntimeOwnership.OFFLINE,
                release_scope=None,
                projector=projector_suggestions.get(entry.name),
                source=DiscoverySource.LOCAL_CATALOG,
            )
        )
    available = {entry.name for entry in entries}
    state = (
        SavedModelState.UNSPECIFIED
        if not saved_model
        else SavedModelState.AVAILABLE
        if saved_model in available
        else SavedModelState.MISSING
    )
    warnings = ["Offline local catalog; entries are not proof of runtime callability."]
    if truncated:
        warnings.append(
            f"Offline local catalog was limited to {MAX_DISCOVERY_MODELS} model entries."
        )
    if warning:
        warnings.insert(0, warning)
    return RuntimeDiscoverySnapshot(
        mode=snapshot.mode,
        owned=snapshot.owned,
        runtime_epoch=snapshot.runtime_epoch,
        endpoint=snapshot.endpoint,
        active_router_root=snapshot.active_router_root,
        models=tuple(models),
        saved_model=saved_model or None,
        saved_model_state=state,
        captured_at=captured_at,
        warnings=tuple(warnings),
    )


def discover_runtime(
    snapshot: RuntimeEndpointSnapshot,
    client: Any | None,
    *,
    saved_model: str = "",
    catalog: ModelCatalog | None = None,
    clock: Callable[[], float] = time.time,
) -> RuntimeDiscoverySnapshot:
    """Capture passive runtime facts without loading or reloading a model."""

    saved_model = saved_model.strip()
    captured_at = clock()
    if client is None or snapshot.mode == RuntimeMode.NONE:
        return _offline_discovery(
            snapshot,
            saved_model,
            catalog=catalog or ModelCatalog(),
            captured_at=captured_at,
        )

    ownership = RuntimeOwnership.OWNED if snapshot.owned else RuntimeOwnership.ATTACHED
    if snapshot.mode != RuntimeMode.ROUTER:
        props = client.passive_props(None)
        capabilities = _unknown_capabilities()
        _apply_props(capabilities, props)
        configured_model_id = (
            Path(snapshot.configured_model_path).name if snapshot.configured_model_path else None
        )
        context = _context_from_props(props)
        if context.state == KnowledgeState.UNKNOWN:
            context = _configured_context(snapshot)
        model_id = (
            props.model_alias
            or (Path(props.model_path).name if props.model_path else None)
            or configured_model_id
            or _RUNNING_MODEL_SENTINEL
        )
        saved_state = SavedModelState.UNSPECIFIED
        if saved_model:
            candidates = {
                model_id,
                props.model_path or "",
                Path(props.model_path).name if props.model_path else "",
                snapshot.configured_model_path or "",
                configured_model_id or "",
            }
            saved_state = (
                SavedModelState.AVAILABLE if saved_model in candidates else SavedModelState.MISSING
            )
        descriptor = RuntimeModelDescriptor(
            model_id=model_id,
            aliases=(),
            residency=ModelState.SLEEPING if props.is_sleeping else ModelState.LOADED,
            availability=SavedModelState.AVAILABLE,
            input_capabilities=capabilities,
            context_length=context,
            model_path=props.model_path or snapshot.configured_model_path,
            ownership=ownership,
            release_scope="direct_runtime" if snapshot.owned else "attached",
            projector=_projector_suggestion(
                catalog,
                _catalog_model_identity(
                    catalog,
                    props.model_path,
                    snapshot.configured_model_path,
                    model_id,
                ),
            ),
            source=DiscoverySource.PROPS,
        )
        return RuntimeDiscoverySnapshot(
            mode=snapshot.mode,
            owned=snapshot.owned,
            runtime_epoch=snapshot.runtime_epoch,
            endpoint=snapshot.endpoint,
            active_router_root=snapshot.active_router_root,
            models=(descriptor,),
            saved_model=saved_model or None,
            saved_model_state=saved_state,
            captured_at=captured_at,
        )

    router_models = tuple(client.models(reload=False))
    if len(router_models) > MAX_DISCOVERY_MODELS:
        raise LlamaClientError(
            f"/models exceeds the passive discovery limit of {MAX_DISCOVERY_MODELS} entries",
            endpoint="/models",
        )
    selected = next(
        (model for model in router_models if saved_model and _matches_saved(model, saved_model)),
        None,
    )
    saved_state = (
        SavedModelState.UNSPECIFIED
        if not saved_model
        else SavedModelState.AVAILABLE
        if selected is not None
        else SavedModelState.MISSING
    )
    selected_props: ServerProps | None = None
    warnings: list[str] = []
    if selected is not None:
        try:
            selected_props = client.passive_props(selected.id)
        except LlamaClientError as exc:
            if not selected.callable and exc.status_code in _PASSIVE_UNAVAILABLE_CODES:
                warnings.append("Selected model is not loaded; passive properties remain Unknown.")
            else:
                raise

    descriptors: list[RuntimeModelDescriptor] = []
    for model in router_models:
        capabilities = _router_capabilities(model)
        # Router presets can override the process-level context independently.
        # Only selected-model /props is authoritative for a router child.
        context = Fact.unknown()
        props = selected_props if selected is not None and model.id == selected.id else None
        if props is not None:
            _apply_props(capabilities, props)
            props_context = _context_from_props(props)
            if props_context.state == KnowledgeState.KNOWN:
                context = props_context
        descriptors.append(
            RuntimeModelDescriptor(
                model_id=model.id,
                aliases=model.aliases,
                residency=model.state,
                availability=SavedModelState.AVAILABLE,
                input_capabilities=capabilities,
                context_length=context,
                model_path=props.model_path if props else None,
                ownership=ownership,
                release_scope="router_model" if snapshot.owned else "attached",
                projector=(
                    _projector_suggestion(catalog, model.id)
                    if selected is not None and model.id == selected.id
                    else None
                ),
                source=DiscoverySource.ROUTER,
            )
        )
    return RuntimeDiscoverySnapshot(
        mode=snapshot.mode,
        owned=snapshot.owned,
        runtime_epoch=snapshot.runtime_epoch,
        endpoint=snapshot.endpoint,
        active_router_root=snapshot.active_router_root,
        models=tuple(descriptors),
        saved_model=saved_model or None,
        saved_model_state=saved_state,
        captured_at=captured_at,
        warnings=tuple(warnings),
    )


__all__ = [
    "DiscoverySource",
    "Fact",
    "KnowledgeState",
    "MAX_DISCOVERY_ALIASES",
    "MAX_DISCOVERY_MODELS",
    "MAX_DISCOVERY_TEXT_CHARS",
    "ProjectorSuggestion",
    "RuntimeDiscoverySnapshot",
    "RuntimeEndpointSnapshot",
    "RuntimeModelDescriptor",
    "RuntimeOwnership",
    "SavedModelState",
    "discover_runtime",
]
