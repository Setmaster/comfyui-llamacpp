"""Safe local projector resolution for direct llama-server launches."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal
from urllib.parse import urlsplit

from .catalog import ModelCatalog, ModelEntry
from .gguf_metadata import (
    MAX_HEADER_BYTES,
    GGUFLimitError,
    GGUFMetadata,
    GGUFMetadataError,
    read_gguf_metadata,
)

AUTO_PROJECTOR = "(auto)"
NONE_PROJECTOR = "(none - text only)"

# Descriptive aliases make call sites readable without creating another value.
AUTO_SELECTION = AUTO_PROJECTOR
NONE_SELECTION = NONE_PROJECTOR

MAX_PROJECTOR_CANDIDATES = 256
MAX_ERROR_CANDIDATES = 6
MAX_EVIDENCE_ITEMS = 6
MAX_EVIDENCE_ITEM_LENGTH = 160
MAX_DISPLAY_NAME_LENGTH = 180
MAX_STATUS_DETAIL_LENGTH = 240
MAX_BASE_MODELS = 32
MAX_RESOLVER_SCAN_BYTES = 128 * 1024 * 1024
SUPPORTED_MINICPMV_VERSIONS = frozenset((6,))

ProjectorMode = Literal["auto", "none", "explicit"]
ProjectorOutcome = Literal["auto_selected", "auto_text_only", "text_only", "explicit"]


class ProjectorResolutionError(ValueError):
    """Automatic or explicit projector selection cannot be resolved safely."""


@dataclass(frozen=True, slots=True)
class ProjectorResolution:
    """The exact lexical model/projector paths and browser-safe provenance."""

    model_name: str
    model_path: Path
    mode: ProjectorMode
    outcome: ProjectorOutcome
    projector_name: str | None = None
    projector_path: Path | None = None
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        selected = self.outcome in {"auto_selected", "explicit"}
        if selected and (
            self.projector_name is None
            or self.projector_path is None
            or not self.projector_name.casefold().endswith(".gguf")
        ):
            raise ValueError("Selected projector resolutions require one .gguf projector")
        if not selected and (self.projector_name is not None or self.projector_path is not None):
            raise ValueError("Text-only projector resolutions cannot contain a projector")

    def status_dict(self) -> dict[str, str]:
        """Return browser-safe status data without absolute filesystem paths."""

        selected = self.outcome in {"auto_selected", "explicit"}
        status = {
            "mode": self.mode,
            "outcome": "selected" if selected else "text_only",
        }
        if selected:
            assert self.projector_name is not None
            status["projector"] = self.projector_name
        if self.evidence:
            detail = "; ".join(self.evidence)
            if len(detail) > MAX_STATUS_DETAIL_LENGTH:
                detail = detail[: MAX_STATUS_DETAIL_LENGTH - 1] + "…"
            status["detail"] = detail
        return status


@dataclass(frozen=True, slots=True)
class _Interface:
    base_width: int
    deepstack_layers: int
    effective_width: int
    deepstack_indices: tuple[int, ...] = ()
    deepstack_signature: tuple[tuple[int, tuple[int, ...], tuple[int, ...]], ...] = ()
    conflict_reason: str | None = None


@dataclass(frozen=True, slots=True)
class _IdentityMatch:
    key: str
    evidence: str


@dataclass(frozen=True, slots=True)
class _Assessment:
    entry: ModelEntry
    status: Literal["compatible", "incompatible", "unresolved", "irrelevant"]
    semantic_identity: tuple[object, ...] | None
    evidence: tuple[str, ...]


_MODEL_FAMILY = {
    "gemma3": "gemma3",
    "gemma4": "gemma4",
    "qwen3vl": "qwen3vl",
    "qwen3vlmoe": "qwen3vl",
    "qwen35": "qwen35",
    "qwen35moe": "qwen35",
}
_PROJECTOR_TYPES = {
    "gemma3": frozenset(("gemma3",)),
    "gemma4": frozenset(("gemma4v", "gemma4uv")),
    "qwen3vl": frozenset(("qwen3vl_merger",)),
    # Current Qwen3.5 uses the Qwen3-VL merger interface.
    "qwen35": frozenset(("qwen3vl_merger",)),
}
_KNOWN_VISION_ARCHITECTURES = frozenset(_MODEL_FAMILY)
_BASE_NAME_KEY = re.compile(r"^general\.base_model\.(\d+)\.name$")
_BASE_REPO_KEY = re.compile(r"^general\.base_model\.(\d+)\.repo_url$")
_DEEPSTACK_TENSOR = re.compile(r"^v\.deepstack\.(\d+)\.fc([12])\.weight$")
_MINICPM_PATH = re.compile(r"(?:^| )minicpm v(?: |$).*\d")
_GENERIC_IDENTITIES = frozenset(
    (
        "",
        "clip",
        "image encoder",
        "mmproj",
        "model",
        "projector",
        "vision",
        "vision model",
        "vision projector",
    )
)


def _display(value: str) -> str:
    normalized = value.strip().replace("\\", "/")
    if len(normalized) <= MAX_DISPLAY_NAME_LENGTH:
        return normalized
    return normalized[: MAX_DISPLAY_NAME_LENGTH - 1] + "…"


def _bounded_evidence(*items: str) -> tuple[str, ...]:
    result: list[str] = []
    for item in items:
        clean = " ".join(item.split())
        if not clean or clean in result:
            continue
        if len(clean) > MAX_EVIDENCE_ITEM_LENGTH:
            clean = clean[: MAX_EVIDENCE_ITEM_LENGTH - 1] + "…"
        result.append(clean)
        if len(result) == MAX_EVIDENCE_ITEMS:
            break
    return tuple(result)


def _bounded_candidate_names(entries: list[ModelEntry]) -> str:
    names = [_display(entry.name) for entry in entries[:MAX_ERROR_CANDIDATES]]
    omitted = len(entries) - len(names)
    if omitted > 0:
        names.append(f"and {omitted} more")
    return ", ".join(names)


def _normalized_identity(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _non_generic(value: str) -> bool:
    if value in _GENERIC_IDENTITIES or len(value) < 4:
        return False
    first = value.split(maxsplit=1)[0]
    return first not in {"mmproj", "model", "projector"}


def _filename_identity(name: str) -> str:
    filename = PurePosixPath(name.replace("\\", "/")).name
    stem = filename[:-5] if filename.casefold().endswith(".gguf") else filename
    tokens = re.findall(r"[a-z0-9]+", stem.casefold())
    tokens = [token for token in tokens if token != "mmproj"]

    if len(tokens) >= 3 and tokens[-2] == "of" and tokens[-1].isdigit():
        tokens = tokens[:-3]

    cutoff: int | None = None
    for index, token in enumerate(tokens):
        is_quant = re.fullmatch(r"q\d+", token) is not None or token in {
            "bf16",
            "f16",
            "f32",
            "fp16",
            "fp32",
        }
        if is_quant and index >= max(0, len(tokens) - 5):
            cutoff = index
            break
    if cutoff is not None:
        tokens = tokens[:cutoff]
    while tokens and re.fullmatch(r"i\d+", tokens[-1]) is not None:
        tokens.pop()
    return " ".join(tokens)


def _valid_lineage_count(metadata: GGUFMetadata) -> int | None:
    count = metadata.integer("general.base_model.count")
    return count if count is not None and 0 < count <= MAX_BASE_MODELS else None


def _lineage_value(
    metadata: GGUFMetadata,
    key: str,
    pattern: re.Pattern[str],
) -> str | None:
    count = _valid_lineage_count(metadata)
    match = pattern.fullmatch(key)
    if count is None or match is None or int(match.group(1)) >= count:
        return None
    value = metadata.values.get(key)
    return value if isinstance(value, str) else None


def _metadata_names(metadata: GGUFMetadata) -> tuple[str, set[str]]:
    current = _normalized_identity(metadata.string("general.name"))
    base_names = {
        normalized
        for key in metadata.values
        if (value := _lineage_value(metadata, key, _BASE_NAME_KEY)) is not None
        and _non_generic(normalized := _normalized_identity(value))
    }
    return current, base_names


def _metadata_repositories(metadata: GGUFMetadata) -> set[str]:
    repositories: set[str] = set()
    for key in metadata.values:
        value = _lineage_value(metadata, key, _BASE_REPO_KEY)
        if value is None:
            continue
        try:
            parsed = urlsplit(value.strip())
            scheme = parsed.scheme.casefold()
            hostname = parsed.hostname
            username = parsed.username
            password = parsed.password
            port = parsed.port
        except ValueError:
            continue
        if (
            scheme not in {"http", "https"}
            or hostname is None
            or username is not None
            or password is not None
        ):
            continue
        host = hostname.casefold().rstrip(".")
        if not host:
            continue
        rendered_host = f"[{host}]" if ":" in host else host
        if port is not None and not (
            (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
        ):
            rendered_host = f"{rendered_host}:{port}"
        path = parsed.path.rstrip("/")
        if path.casefold().endswith(".git"):
            path = path[:-4]
        if path.count("/") < 2:
            continue
        repositories.add(f"{rendered_host}{path.casefold()}")
    return repositories


def _identity_match(
    model_metadata: GGUFMetadata,
    model_name: str,
    projector_metadata: GGUFMetadata,
    projector_name: str,
    *,
    allow_filename: bool,
) -> _IdentityMatch | None:
    model_current, model_bases = _metadata_names(model_metadata)
    projector_current, projector_bases = _metadata_names(projector_metadata)

    if _non_generic(model_current) and model_current == projector_current:
        return _IdentityMatch(f"name:{model_current}", f"exact metadata name: {model_current}")
    if _non_generic(projector_current) and projector_current in model_bases:
        return _IdentityMatch(
            f"base-name:{projector_current}",
            f"projector name matches model lineage: {projector_current}",
        )
    if _non_generic(model_current) and model_current in projector_bases:
        return _IdentityMatch(
            f"base-name:{model_current}",
            f"model name matches projector lineage: {model_current}",
        )

    shared_base_names = model_bases & projector_bases
    if shared_base_names:
        shared = min(shared_base_names)
        return _IdentityMatch(f"base-name:{shared}", f"shared base model name: {shared}")

    shared_repositories = _metadata_repositories(model_metadata) & _metadata_repositories(
        projector_metadata
    )
    if shared_repositories:
        shared = min(shared_repositories)
        return _IdentityMatch(f"base-repo:{shared}", f"shared base repository: {shared}")

    if allow_filename:
        model_filename = _filename_identity(model_name)
        projector_filename = _filename_identity(projector_name)
        if _non_generic(model_filename) and model_filename == projector_filename:
            return _IdentityMatch(
                f"filename:{model_filename}", f"exact normalized filename: {model_filename}"
            )
    return None


def _quant_stripped_identity(
    metadata: GGUFMetadata,
    name: str,
    identity_match: _IdentityMatch,
) -> str:
    current, base_names = _metadata_names(metadata)
    if base_names:
        return f"model:{min(base_names)}"
    if _non_generic(current):
        return f"model:{current}"
    repositories = _metadata_repositories(metadata)
    if repositories:
        return f"base-repo:{min(repositories)}"
    filename = _filename_identity(name)
    if _non_generic(filename):
        return f"filename:{filename}"
    return identity_match.key


def _semantic_identity(
    metadata: GGUFMetadata,
    name: str,
    identity_match: _IdentityMatch,
) -> tuple[object, ...]:
    projector_type, _ = _projector_type(metadata)
    interface = _projector_interface(metadata)
    resampler = tuple(
        tensor.dimensions for tensor in metadata.tensors if tensor.name == "resampler.proj.weight"
    )
    output_tensors = tuple(
        (tensor.name, tensor.dimensions)
        for tensor in metadata.tensors
        if tensor.name in {"mm.2.bias", "mm.input_projection.weight"}
    )
    return (
        "projector",
        _quant_stripped_identity(metadata, name, identity_match),
        projector_type,
        interface.base_width if interface else None,
        interface.effective_width if interface else None,
        metadata.integer("clip.vision.embedding_length"),
        interface.deepstack_indices if interface else (),
        interface.deepstack_signature if interface else (),
        metadata.integer("clip.minicpmv_version"),
        resampler,
        output_tensors,
    )


def _model_interface(metadata: GGUFMetadata) -> _Interface | None:
    architecture = (metadata.string("general.architecture") or "").casefold()
    if not architecture:
        return None
    base_width = metadata.integer(f"{architecture}.embedding_length")
    if base_width is None or base_width <= 0:
        return None
    deepstack = metadata.integer(f"{architecture}.n_deepstack_layers")
    if deepstack is None:
        deepstack = 0
    if deepstack < 0 or deepstack > 1_024:
        return None
    effective_width = base_width * (1 + deepstack)
    if effective_width <= 0 or effective_width > 1_000_000_000:
        return None
    return _Interface(base_width, deepstack, effective_width)


def _projector_interface(metadata: GGUFMetadata) -> _Interface | None:
    base_width = metadata.integer("clip.vision.projection_dim")
    if base_width is None or base_width <= 0:
        return None

    flags = metadata.booleans("clip.vision.is_deepstack_layers")
    flags_present = "clip.vision.is_deepstack_layers" in metadata.values
    flag_indices = (
        tuple(index for index, enabled in enumerate(flags) if enabled)
        if flags is not None
        else None
    )
    fc1: dict[int, tuple[int, ...]] = {}
    fc2: dict[int, tuple[int, ...]] = {}
    for tensor in metadata.tensors:
        match = _DEEPSTACK_TENSOR.fullmatch(tensor.name)
        if match is None:
            continue
        target = fc1 if match.group(2) == "1" else fc2
        target[int(match.group(1))] = tensor.dimensions

    fc1_indices = set(fc1)
    fc2_indices = set(fc2)
    conflict_reason = (
        "deepstack flags are present but are not a boolean array"
        if flags_present and flags is None
        else None
    )
    if fc1_indices != fc2_indices:
        conflict_reason = "deepstack fc1/fc2 tensor descriptors are not paired"

    signature: list[tuple[int, tuple[int, ...], tuple[int, ...]]] = []
    for index in sorted(fc1_indices & fc2_indices):
        fc1_dimensions = fc1[index]
        fc2_dimensions = fc2[index]
        signature.append((index, fc1_dimensions, fc2_dimensions))
        if len(fc1_dimensions) != 2 or len(fc2_dimensions) != 2:
            conflict_reason = "deepstack tensors must be rank 2"
            continue
        if fc1_dimensions[0] != fc1_dimensions[1]:
            conflict_reason = "deepstack fc1 tensor dimensions are inconsistent"
        if fc2_dimensions[0] != fc1_dimensions[0]:
            conflict_reason = "deepstack fc1/fc2 hidden dimensions disagree"
        if fc2_dimensions[1] != base_width:
            conflict_reason = "deepstack fc2 output width disagrees with projection_dim"

    tensor_indices = tuple(sorted(fc1_indices))
    if flag_indices is not None and set(flag_indices) != fc1_indices:
        conflict_reason = "deepstack flags do not exactly match tensor indices"

    deepstack_indices = flag_indices if flag_indices is not None else tensor_indices
    deepstack = len(deepstack_indices)
    effective_width = base_width * (1 + deepstack)
    if effective_width > 1_000_000_000:
        return None
    return _Interface(
        base_width,
        deepstack,
        effective_width,
        deepstack_indices,
        tuple(signature),
        conflict_reason,
    )


def _projector_type(metadata: GGUFMetadata) -> tuple[str, bool]:
    primary = (metadata.string("clip.projector_type") or "").casefold()
    vision = (metadata.string("clip.vision.projector_type") or "").casefold()
    return primary or vision, bool(primary and vision and primary != vision)


def _modern_output_interface(
    family: str,
    metadata: GGUFMetadata,
    interface: _Interface,
) -> tuple[Literal["compatible", "incompatible", "unresolved"], tuple[str, ...]]:
    if family in {"gemma3", "gemma4"}:
        tensor_name = "mm.input_projection.weight"
        expected_rank = 2
        width_axis = 0 if family == "gemma3" else 1
        multiplier = 1
    else:
        tensor_name = "mm.2.bias"
        expected_rank = 1
        width_axis = 0
        multiplier = 1 + interface.deepstack_layers

    tensors = [tensor for tensor in metadata.tensors if tensor.name == tensor_name]
    if len(tensors) != 1:
        return "unresolved", (f"projector output tensor {tensor_name} is missing",)
    dimensions = tensors[0].dimensions
    if len(dimensions) != expected_rank:
        return "unresolved", (
            f"projector output tensor {tensor_name} must have rank {expected_rank}",
        )

    tensor_base_width = dimensions[width_axis]
    tensor_effective_width = tensor_base_width * multiplier
    if tensor_base_width != interface.base_width:
        return "incompatible", (
            "projector output tensor width conflicts with projection_dim "
            f"({tensor_base_width} vs {interface.base_width})",
        )
    if tensor_effective_width != interface.effective_width:
        return "incompatible", (
            "projector output tensor effective width conflicts with metadata "
            f"({tensor_effective_width} vs {interface.effective_width})",
        )
    return "compatible", (f"output-interface={tensor_effective_width} from {tensor_name}",)


def _modern_structure(
    model_metadata: GGUFMetadata,
    projector_metadata: GGUFMetadata,
) -> tuple[Literal["compatible", "incompatible", "unresolved"], tuple[str, ...]]:
    model_architecture = (model_metadata.string("general.architecture") or "").casefold()
    family = _MODEL_FAMILY.get(model_architecture)
    if family is None:
        return "unresolved", ("model family is not recognized for automatic vision pairing",)

    projector_architecture = (projector_metadata.string("general.architecture") or "").casefold()
    if projector_architecture and projector_architecture != "clip":
        return "incompatible", ("projector architecture conflicts with clip",)
    if not projector_architecture:
        return "unresolved", ("projector architecture is missing",)

    general_type = (projector_metadata.string("general.type") or "").casefold()
    if general_type and general_type != "mmproj":
        return "incompatible", ("projector general.type conflicts with mmproj",)
    if not general_type:
        return "unresolved", ("projector general.type is missing",)

    has_vision = projector_metadata.boolean("clip.has_vision_encoder")
    if has_vision is False:
        return "incompatible", ("projector explicitly has no vision encoder",)
    if has_vision is not True:
        return "unresolved", ("projector vision capability is missing",)

    projector_type, projector_type_conflict = _projector_type(projector_metadata)
    if projector_type_conflict:
        return "unresolved", ("projector type metadata conflicts",)
    if not projector_type:
        return "unresolved", ("projector type is missing",)
    if projector_type not in _PROJECTOR_TYPES[family]:
        return "incompatible", (
            f"projector type {projector_type} conflicts with model family {family}",
        )

    model_interface = _model_interface(model_metadata)
    projector_interface = _projector_interface(projector_metadata)
    if model_interface is None or projector_interface is None:
        return "unresolved", ("model/projector interface width is incomplete",)
    if projector_interface.conflict_reason:
        return "unresolved", (projector_interface.conflict_reason,)
    if model_interface.deepstack_layers > 0 and not projector_interface.deepstack_signature:
        return "unresolved", ("projector deepstack tensor descriptors are missing",)
    output_status, output_evidence = _modern_output_interface(
        family,
        projector_metadata,
        projector_interface,
    )
    if output_status != "compatible":
        return output_status, output_evidence
    if model_interface.base_width != projector_interface.base_width:
        return "incompatible", (
            "base interface width mismatch "
            f"({model_interface.base_width} vs {projector_interface.base_width})",
        )
    if model_interface.deepstack_layers != projector_interface.deepstack_layers:
        return "incompatible", (
            "deepstack layer mismatch "
            f"({model_interface.deepstack_layers} vs {projector_interface.deepstack_layers})",
        )
    if model_interface.effective_width != projector_interface.effective_width:
        return "incompatible", (
            "effective interface width mismatch "
            f"({model_interface.effective_width} vs {projector_interface.effective_width})",
        )
    return "compatible", (
        f"family={family}",
        f"interface={model_interface.effective_width}",
        *output_evidence,
    )


def _minicpm_path(name: str) -> bool:
    return _MINICPM_PATH.search(_normalized_identity(name)) is not None


def _legacy_minicpm_structure(
    model_name: str,
    model_metadata: GGUFMetadata,
    projector_metadata: GGUFMetadata,
) -> tuple[Literal["compatible", "incompatible", "unresolved"], tuple[str, ...]] | None:
    description = _normalized_identity(projector_metadata.string("general.description"))
    projector_type, projector_type_conflict = _projector_type(projector_metadata)
    looks_related = (
        _minicpm_path(model_name) or "minicpm v" in description or projector_type == "resampler"
    )
    if not looks_related:
        return None

    if not _minicpm_path(model_name):
        return "unresolved", ("MiniCPM model identity is missing from the local model name",)
    if (model_metadata.string("general.architecture") or "").casefold() != "qwen3":
        return "incompatible", ("MiniCPM legacy model architecture is not qwen3",)
    if "image encoder for minicpm v" not in description:
        return "unresolved", ("MiniCPM projector description is missing",)
    if projector_metadata.boolean("clip.has_vision_encoder") is not True:
        return "unresolved", ("MiniCPM projector vision flag is missing",)
    if projector_metadata.boolean("clip.has_minicpmv_projector") is not True:
        return "unresolved", ("MiniCPM projector capability flag is missing",)
    minicpmv_version = projector_metadata.integer("clip.minicpmv_version")
    if minicpmv_version not in SUPPORTED_MINICPMV_VERSIONS:
        return "unresolved", ("MiniCPM projector version is unsupported or missing",)
    if projector_type_conflict:
        return "unresolved", ("MiniCPM projector type metadata conflicts",)
    if projector_type != "resampler":
        return "incompatible", ("MiniCPM projector type is not resampler",)
    projector_architecture = (projector_metadata.string("general.architecture") or "").casefold()
    if projector_architecture and projector_architecture != "clip":
        return "incompatible", ("MiniCPM projector architecture conflicts with clip",)

    model_interface = _model_interface(model_metadata)
    tensors = [
        tensor for tensor in projector_metadata.tensors if tensor.name == "resampler.proj.weight"
    ]
    if model_interface is None or len(tensors) != 1:
        return "unresolved", ("MiniCPM resampler interface descriptor is incomplete",)
    dimensions = tensors[0].dimensions
    if len(dimensions) != 2:
        return "incompatible", ("MiniCPM resampler projection tensor is not rank 2",)
    if dimensions != (model_interface.base_width, model_interface.base_width):
        return "incompatible", (
            "MiniCPM resampler width mismatch "
            f"({dimensions[0]}x{dimensions[1]} vs {model_interface.base_width})",
        )
    return "compatible", (
        "family=minicpm-v-legacy",
        f"resampler-interface={model_interface.base_width}",
    )


def _known_vision_model(
    model_name: str,
    metadata: GGUFMetadata | None,
) -> bool:
    if _minicpm_path(model_name):
        return True
    if metadata is None:
        return False
    architecture = (metadata.string("general.architecture") or "").casefold()
    if architecture in _KNOWN_VISION_ARCHITECTURES:
        return True
    tags = metadata.strings("general.tags") or ()
    normalized_tags = {_normalized_identity(tag) for tag in tags}
    return bool(
        normalized_tags
        & {
            "image text to text",
            "image to text",
            "vision language model",
        }
    )


def _adjacent_name_plausible(model_name: str, projector_name: str) -> bool:
    model_identity = _filename_identity(model_name)
    projector_identity = _filename_identity(projector_name)
    return _non_generic(model_identity) and model_identity == projector_identity


def _lexical_path(entry: ModelEntry) -> Path:
    return entry.root.joinpath(*PurePosixPath(entry.name).parts)


def _entry_by_name(
    entries: list[ModelEntry],
    name: str,
    *,
    projector: bool,
) -> ModelEntry:
    normalized = name.strip().replace("\\", "/")
    for entry in entries:
        if entry.name == normalized and entry.is_mmproj is projector:
            return entry
    kind = "projector" if projector else "model"
    raise ProjectorResolutionError(
        f"Configured llama.cpp {kind} was not found: {_display(normalized)}"
    )


@dataclass(slots=True)
class _ScanBudget:
    remaining: int = field(default_factory=lambda: MAX_RESOLVER_SCAN_BYTES)

    def read(self, path: Path) -> GGUFMetadata:
        if self.remaining <= 0:
            raise ProjectorResolutionError(
                "Automatic projector selection exceeded its aggregate GGUF scan budget; "
                "select a projector explicitly"
            )
        scan_limit = min(MAX_HEADER_BYTES, self.remaining)
        try:
            metadata = read_gguf_metadata(path, scan_limit=scan_limit)
        except GGUFMetadataError as exc:
            self.remaining = max(0, self.remaining - exc.scanned_bytes)
            if isinstance(exc, GGUFLimitError) and scan_limit < MAX_HEADER_BYTES:
                raise ProjectorResolutionError(
                    "Automatic projector selection exceeded its aggregate GGUF scan budget; "
                    "select a projector explicitly"
                ) from exc
            raise
        self.remaining -= metadata.scanned_bytes
        return metadata


def _metadata_or_error(
    path: Path,
    budget: _ScanBudget,
) -> tuple[GGUFMetadata | None, GGUFMetadataError | None]:
    try:
        return budget.read(path), None
    except GGUFMetadataError as exc:
        return None, exc


def _assess_candidate(
    model_entry: ModelEntry,
    model_metadata: GGUFMetadata,
    projector_entry: ModelEntry,
    projector_metadata: GGUFMetadata,
    *,
    adjacent: bool,
) -> _Assessment:
    legacy = (
        _legacy_minicpm_structure(
            model_entry.name,
            model_metadata,
            projector_metadata,
        )
        if adjacent
        else None
    )
    if legacy is not None:
        status, evidence = legacy
        resampler = tuple(
            tensor.dimensions
            for tensor in projector_metadata.tensors
            if tensor.name == "resampler.proj.weight"
        )
        semantic = (
            (
                "minicpm",
                _filename_identity(model_entry.name),
                projector_metadata.integer("clip.minicpmv_version"),
                _projector_type(projector_metadata)[0],
                resampler,
            )
            if status == "compatible"
            else None
        )
        return _Assessment(projector_entry, status, semantic, evidence)

    identity = _identity_match(
        model_metadata,
        model_entry.name,
        projector_metadata,
        projector_entry.name,
        allow_filename=adjacent,
    )
    if identity is None and not adjacent:
        return _Assessment(projector_entry, "irrelevant", None, ())

    structural_status, structural_evidence = _modern_structure(model_metadata, projector_metadata)
    if structural_status == "incompatible":
        return _Assessment(projector_entry, "incompatible", None, structural_evidence)
    quantization_conflict = _quantization_conflict(projector_entry, projector_metadata)
    if structural_status == "unresolved" or identity is None or quantization_conflict:
        evidence = structural_evidence
        if identity is None:
            evidence = (*evidence, "strong model/projector identity is missing")
        if quantization_conflict:
            evidence = (*evidence, "projector filename quantization conflicts with metadata")
        return _Assessment(projector_entry, "unresolved", None, evidence)

    return _Assessment(
        projector_entry,
        "compatible",
        _semantic_identity(projector_metadata, projector_entry.name, identity),
        _bounded_evidence(
            identity.evidence,
            *structural_evidence,
            "adjacent projector" if adjacent else "catalog-wide identity match",
        ),
    )


_FILE_TYPE_QUANTIZATION = {
    7: (0, "Q8_0"),
    32: (1, "BF16"),
    1: (2, "F16"),
    0: (3, "F32"),
}


def _filename_quantization(entry: ModelEntry) -> tuple[int, str, int] | None:
    filename = PurePosixPath(entry.name.replace("\\", "/")).name
    normalized = re.sub(r"[^a-z0-9]+", "_", filename.casefold()).strip("_")
    if re.search(r"(?:^|_)q8_0(?:_|$)", normalized):
        return 0, "Q8_0", 7
    if re.search(r"(?:^|_)bf16(?:_|$)", normalized):
        return 1, "BF16", 32
    if re.search(r"(?:^|_)f16(?:_|$)", normalized):
        return 2, "F16", 1
    if re.search(r"(?:^|_)f32(?:_|$)", normalized):
        return 3, "F32", 0
    return None


def _quantization_conflict(entry: ModelEntry, metadata: GGUFMetadata) -> bool:
    filename = _filename_quantization(entry)
    file_type = metadata.integer("general.file_type")
    return (
        filename is not None and "general.file_type" in metadata.values and file_type != filename[2]
    )


def _quantization_rank(entry: ModelEntry, metadata: GGUFMetadata) -> tuple[int, str]:
    file_type = metadata.integer("general.file_type")
    if "general.file_type" in metadata.values:
        metadata_rank = _FILE_TYPE_QUANTIZATION.get(file_type)
        return (
            metadata_rank[0] if metadata_rank is not None else 10,
            entry.name.casefold(),
        )
    filename = _filename_quantization(entry)
    return (filename[0] if filename is not None else 10, entry.name.casefold())


def _stable_lexical(entry: ModelEntry) -> Path:
    lexical = _lexical_path(entry)
    try:
        if lexical.resolve(strict=True) != entry.path:
            raise ProjectorResolutionError(
                f"Configured llama.cpp file changed during resolution: {_display(entry.name)}"
            )
    except OSError as exc:
        raise ProjectorResolutionError(
            f"Configured llama.cpp file became unavailable: {_display(entry.name)}"
        ) from exc
    return lexical


def resolve_direct_projector(
    model_name: str,
    selection: str,
    *,
    catalog: ModelCatalog | None = None,
) -> ProjectorResolution:
    """Resolve one direct model/projector selection without network access."""

    active_catalog = catalog or ModelCatalog()
    entries = active_catalog.entries()
    model_entry = _entry_by_name(entries, model_name, projector=False)
    lexical_model = _stable_lexical(model_entry)

    if not selection.strip() or selection == NONE_PROJECTOR:
        return ProjectorResolution(
            model_name=model_entry.name,
            model_path=lexical_model,
            mode="none",
            outcome="text_only",
            evidence=(
                "legacy blank text-only selection"
                if not selection.strip()
                else "explicit text-only selection",
            ),
        )

    if selection != AUTO_PROJECTOR:
        projector_entry = _entry_by_name(entries, selection, projector=True)
        return ProjectorResolution(
            model_name=model_entry.name,
            model_path=lexical_model,
            mode="explicit",
            outcome="explicit",
            projector_name=projector_entry.name,
            projector_path=_stable_lexical(projector_entry),
            evidence=("exact manual projector selection",),
        )

    projector_entries = [entry for entry in entries if entry.is_mmproj]
    scan_budget = _ScanBudget()
    model_metadata, model_error = _metadata_or_error(model_entry.path, scan_budget)
    if model_metadata is None:
        plausible = [
            entry
            for entry in projector_entries
            if (
                entry.root == model_entry.root
                and _lexical_path(entry).parent == lexical_model.parent
            )
        ]
        if plausible:
            raise ProjectorResolutionError(
                "Automatic projector selection cannot inspect model metadata for "
                f"'{_display(model_entry.name)}' while plausible projectors exist: "
                f"{_bounded_candidate_names(plausible)}. Select one explicitly"
            ) from model_error
        return ProjectorResolution(
            model_name=model_entry.name,
            model_path=lexical_model,
            mode="auto",
            outcome="auto_text_only",
            evidence=("model metadata unavailable; no plausible local projector",),
        )

    if not _known_vision_model(model_entry.name, model_metadata):
        if len(projector_entries) > MAX_PROJECTOR_CANDIDATES:
            raise ProjectorResolutionError(
                "Automatic projector selection stopped because the local catalog contains "
                f"more than {MAX_PROJECTOR_CANDIDATES} projectors; select one explicitly"
            )
        plausible: list[ModelEntry] = []
        for projector_entry in projector_entries:
            adjacent = (
                projector_entry.root == model_entry.root
                and _lexical_path(projector_entry).parent == lexical_model.parent
            )
            projector_metadata, _projector_error = _metadata_or_error(
                projector_entry.path,
                scan_budget,
            )
            if projector_metadata is None:
                if adjacent and _adjacent_name_plausible(
                    model_entry.name,
                    projector_entry.name,
                ):
                    plausible.append(projector_entry)
                continue
            if (
                _identity_match(
                    model_metadata,
                    model_entry.name,
                    projector_metadata,
                    projector_entry.name,
                    allow_filename=adjacent,
                )
                is not None
            ):
                plausible.append(projector_entry)
        if plausible:
            raise ProjectorResolutionError(
                "Automatic projector selection cannot classify the model family for "
                f"'{_display(model_entry.name)}' while plausible local projectors exist: "
                f"{_bounded_candidate_names(plausible)}. Select one explicitly or choose "
                f"'{NONE_PROJECTOR}'"
            )
        return ProjectorResolution(
            model_name=model_entry.name,
            model_path=lexical_model,
            mode="auto",
            outcome="auto_text_only",
            evidence=("model metadata is not a known vision family; no plausible local projector",),
        )

    if len(projector_entries) > MAX_PROJECTOR_CANDIDATES:
        raise ProjectorResolutionError(
            "Automatic projector selection stopped because the local catalog contains "
            f"more than {MAX_PROJECTOR_CANDIDATES} projectors; select one explicitly"
        )

    assessments: list[_Assessment] = []
    metadata_by_name: dict[str, GGUFMetadata] = {}
    for projector_entry in projector_entries:
        adjacent = (
            projector_entry.root == model_entry.root
            and _lexical_path(projector_entry).parent == lexical_model.parent
        )
        projector_metadata, _projector_error = _metadata_or_error(
            projector_entry.path,
            scan_budget,
        )
        if projector_metadata is None:
            if adjacent and _adjacent_name_plausible(
                model_entry.name,
                projector_entry.name,
            ):
                assessments.append(
                    _Assessment(
                        projector_entry,
                        "unresolved",
                        None,
                        ("projector metadata is unreadable",),
                    )
                )
            continue
        metadata_by_name[projector_entry.name] = projector_metadata
        assessments.append(
            _assess_candidate(
                model_entry,
                model_metadata,
                projector_entry,
                projector_metadata,
                adjacent=adjacent,
            )
        )

    unresolved = [
        assessment.entry for assessment in assessments if assessment.status == "unresolved"
    ]
    if unresolved:
        raise ProjectorResolutionError(
            "Automatic projector selection is inconclusive for "
            f"'{_display(model_entry.name)}'; unresolved plausible projectors: "
            f"{_bounded_candidate_names(unresolved)}. Select one explicitly or choose "
            f"'{NONE_PROJECTOR}'"
        )

    compatible = [assessment for assessment in assessments if assessment.status == "compatible"]

    grouped: dict[tuple[object, ...], list[_Assessment]] = {}
    for assessment in compatible:
        assert assessment.semantic_identity is not None
        grouped.setdefault(assessment.semantic_identity, []).append(assessment)

    if len(grouped) > 1:
        candidates = [item.entry for group in grouped.values() for item in group]
        raise ProjectorResolutionError(
            "Automatic projector selection is ambiguous for "
            f"'{_display(model_entry.name)}'; distinct compatible projector identities: "
            f"{_bounded_candidate_names(candidates)}. Select one explicitly"
        )

    if grouped:
        group = next(iter(grouped.values()))
        selected = min(
            group,
            key=lambda assessment: _quantization_rank(
                assessment.entry,
                metadata_by_name[assessment.entry.name],
            ),
        )
        selected_metadata = metadata_by_name[selected.entry.name]
        rank = _quantization_rank(selected.entry, selected_metadata)[0]
        quantization = {
            0: "preferred quantization=Q8_0",
            1: "preferred quantization=BF16",
            2: "preferred quantization=F16",
            3: "preferred quantization=F32",
        }.get(rank, "deterministic projector selection")
        return ProjectorResolution(
            model_name=model_entry.name,
            model_path=lexical_model,
            mode="auto",
            outcome="auto_selected",
            projector_name=selected.entry.name,
            projector_path=_stable_lexical(selected.entry),
            evidence=_bounded_evidence(*selected.evidence, quantization),
        )

    if _known_vision_model(model_entry.name, model_metadata):
        incompatible = [
            assessment.entry for assessment in assessments if assessment.status == "incompatible"
        ]
        detail = (
            f" Nearby incompatible candidates: {_bounded_candidate_names(incompatible)}."
            if incompatible
            else ""
        )
        raise ProjectorResolutionError(
            "No compatible installed projector was found for known vision model "
            f"'{_display(model_entry.name)}'.{detail} Install the matching projector, "
            f"select one explicitly, or choose '{NONE_PROJECTOR}'"
        )

    return ProjectorResolution(
        model_name=model_entry.name,
        model_path=lexical_model,
        mode="auto",
        outcome="auto_text_only",
        evidence=("no compatible local projector; model is not known to require vision",),
    )


__all__ = [
    "AUTO_PROJECTOR",
    "AUTO_SELECTION",
    "NONE_PROJECTOR",
    "NONE_SELECTION",
    "ProjectorResolution",
    "ProjectorResolutionError",
    "resolve_direct_projector",
]
