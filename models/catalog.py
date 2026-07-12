"""Comfy-aware, containment-safe GGUF model catalog."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FOLDER_KEY = "llamacpp"
LEGACY_FOLDER_KEYS = ("LLM", "llm")


class ModelCatalogError(ValueError):
    pass


def _folder_paths_module() -> Any | None:
    try:
        import folder_paths
    except ImportError:
        return None
    return folder_paths


def get_comfyui_root() -> Path:
    folder_paths = _folder_paths_module()
    if folder_paths is not None:
        base_path = getattr(folder_paths, "base_path", None)
        if base_path:
            return Path(base_path).resolve()
    return Path(__file__).resolve().parents[3]


def get_default_models_directory() -> Path:
    folder_paths = _folder_paths_module()
    models_dir = getattr(folder_paths, "models_dir", None) if folder_paths else None
    base = Path(models_dir).resolve() if models_dir else get_comfyui_root() / "models"
    return (base / "LLM" / "gguf").resolve()


def _gguf_root_from_llm_path(path: str | os.PathLike[str]) -> Path:
    """Map a configured Comfy LLM root to this pack's GGUF collection.

    Existing Comfy installations commonly register an ``LLM`` root through
    ``extra_model_paths.yaml`` and keep llama.cpp models in its ``gguf``
    child. A path that already names ``gguf`` is used directly. If no child
    exists, the configured root itself remains supported for older layouts.
    """

    root = Path(path).expanduser().resolve()
    if root.name.casefold() == "gguf":
        return root
    child = (root / "gguf").resolve()
    return child if child.is_dir() else root


def _existing_llm_roots(folder_paths: Any) -> list[Path]:
    roots: list[Path] = []
    for folder_key in LEGACY_FOLDER_KEYS:
        try:
            configured = folder_paths.get_folder_paths(folder_key)
        except (KeyError, AttributeError):
            continue
        for raw_path in configured:
            root = _gguf_root_from_llm_path(raw_path)
            if root not in roots:
                roots.append(root)
    return roots


def register_model_folder() -> None:
    folder_paths = _folder_paths_module()
    if folder_paths is None:
        return
    default = str(get_default_models_directory())
    try:
        existing = list(folder_paths.get_folder_paths(FOLDER_KEY))
    except (KeyError, AttributeError):
        existing = []
    ordered = [*existing, *(str(root) for root in _existing_llm_roots(folder_paths)), default]
    registered = set(existing)
    for path in ordered:
        if path in registered:
            continue
        folder_paths.add_model_folder_path(FOLDER_KEY, path)
        registered.add(path)


def _configured_roots() -> list[Path]:
    register_model_folder()
    folder_paths = _folder_paths_module()
    raw_paths: Iterable[str]
    if folder_paths is not None:
        try:
            raw_paths = folder_paths.get_folder_paths(FOLDER_KEY)
        except (KeyError, AttributeError):
            raw_paths = [str(get_default_models_directory())]
    else:
        raw_paths = [str(get_default_models_directory())]

    roots: list[Path] = []
    for raw_path in raw_paths:
        root = Path(raw_path).expanduser().resolve()
        if root not in roots:
            roots.append(root)
    return roots


def _safe_relative_name(name: str) -> Path:
    if not isinstance(name, str) or not name.strip() or "\x00" in name:
        raise ModelCatalogError("Model name must be a non-empty relative path")
    normalized = name.strip().replace("\\", "/")
    relative = Path(normalized)
    if relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts):
        raise ModelCatalogError(f"Unsafe model path: {name!r}")
    return relative


def _contained(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class ModelEntry:
    name: str
    path: Path
    root: Path
    is_mmproj: bool


class ModelCatalog:
    def __init__(self, roots: Iterable[str | os.PathLike[str]] | None = None):
        configured = tuple(roots) if roots is not None else tuple(_configured_roots())
        self.roots = tuple(dict.fromkeys(Path(root).expanduser().resolve() for root in configured))
        fallback = (
            self.roots[0] if roots is not None and self.roots else get_default_models_directory()
        )
        self._default_root = fallback.resolve()

    @property
    def default_root(self) -> Path:
        return self._default_root

    def ensure_default_root(self) -> Path:
        self.default_root.mkdir(parents=True, exist_ok=True)
        return self.default_root

    @staticmethod
    def _router_preset_count(root: Path) -> int:
        """Count safe, unambiguous presets in llama-server's one-level scan."""

        if not root.is_dir():
            return 0
        count = 0
        try:
            children = tuple(root.iterdir())
        except OSError:
            return 0
        for child in children:
            try:
                if child.is_file():
                    # Current llama.cpp checks this suffix case-sensitively and
                    # treats every root-level GGUF as a preset. Ignore projector
                    # files and symlink escapes even though upstream does not.
                    actual = child.resolve(strict=True)
                    if (
                        child.name.endswith(".gguf")
                        and "mmproj" not in child.name
                        and _contained(root, actual)
                    ):
                        count += 1
                    continue
                if not child.is_dir():
                    continue
                directory = child.resolve(strict=True)
                if not _contained(root, directory):
                    continue
                ggufs = tuple(
                    candidate
                    for candidate in directory.iterdir()
                    if candidate.is_file()
                    and candidate.name.endswith(".gguf")
                    and _contained(root, candidate.resolve(strict=True))
                )
                models = tuple(item for item in ggufs if "mmproj" not in item.name)
                projectors = tuple(item for item in ggufs if "mmproj" in item.name)
                if len(projectors) > 1:
                    continue
                if len(models) == 1 or ModelCatalog._is_complete_shard_bundle(models):
                    count += 1
            except OSError:
                continue
        return count

    @staticmethod
    def _is_complete_shard_bundle(models: tuple[Path, ...]) -> bool:
        if not models:
            return False
        pattern = re.compile(r"^(?P<base>.+)-(?P<index>\d{5})-of-(?P<total>\d{5})\.gguf$")
        parsed = [pattern.fullmatch(model.name) for model in models]
        if any(match is None for match in parsed):
            return False
        matches = [match for match in parsed if match is not None]
        identities = {(match["base"], int(match["total"])) for match in matches}
        if len(identities) != 1:
            return False
        _, total = identities.pop()
        indices = {int(match["index"]) for match in matches}
        return total > 0 and indices == set(range(1, total + 1))

    def preferred_router_root(self) -> Path:
        """Choose the configured root exposing the most current router presets."""

        if not self.roots:
            return get_default_models_directory()
        ranked = [
            (self._router_preset_count(root), -index, root) for index, root in enumerate(self.roots)
        ]
        count, _, root = max(ranked, key=lambda item: (item[0], item[1]))
        if count:
            return root
        return next(
            (candidate for candidate in self.roots if candidate.is_dir()), self.default_root
        )

    def count_router_presets(self) -> int:
        """Count safe presets visible across the configured one-level router roots."""

        return sum(self._router_preset_count(root) for root in self.roots)

    def resolve_router_root(self, selection: str | os.PathLike[str] | None = None) -> Path:
        """Resolve an optional configured router root, or select the best populated root."""

        value = os.fspath(selection).strip() if selection is not None else ""
        if not value or value == "(auto)":
            root = self.preferred_router_root()
            if root == self.default_root and not root.exists():
                return self.ensure_default_root()
            return root

        requested = Path(value).expanduser().resolve()
        for root in self.roots:
            if os.path.normcase(str(root)) == os.path.normcase(str(requested)):
                if not root.is_dir():
                    raise ModelCatalogError(
                        f"Configured router model folder does not exist: {root}"
                    )
                return root
        raise ModelCatalogError(
            "Router model folder must be one of ComfyUI's configured llama.cpp roots"
        )

    def entries(self) -> list[ModelEntry]:
        found: dict[str, ModelEntry] = {}
        for root in self.roots:
            if not root.is_dir():
                continue
            for directory, _, files in os.walk(root, followlinks=False):
                for filename in files:
                    if not filename.lower().endswith(".gguf"):
                        continue
                    lexical = Path(directory) / filename
                    try:
                        actual = lexical.resolve(strict=True)
                    except OSError:
                        continue
                    if not actual.is_file() or not _contained(root, actual):
                        continue
                    name = lexical.relative_to(root).as_posix()
                    found.setdefault(
                        name,
                        ModelEntry(name, actual, root, "mmproj" in filename.lower()),
                    )
        return [found[name] for name in sorted(found, key=str.casefold)]

    def list_models(self) -> list[str]:
        return [entry.name for entry in self.entries() if not entry.is_mmproj]

    def list_mmproj(self) -> list[str]:
        return [entry.name for entry in self.entries() if entry.is_mmproj]

    def resolve(self, name: str, *, require_file: bool = True) -> Path:
        relative = _safe_relative_name(name)
        for root in self.roots:
            candidate = (root / relative).resolve()
            if not _contained(root, candidate):
                continue
            if not require_file or candidate.is_file():
                return candidate
        raise ModelCatalogError(f"Model not found in configured llama.cpp folders: {name}")

    def candidate(self, name: str) -> Path:
        """Return a safe path under the default root without requiring a file."""

        relative = _safe_relative_name(name)
        candidate = (self.default_root / relative).resolve()
        if not _contained(self.default_root, candidate):
            raise ModelCatalogError(f"Unsafe model path: {name!r}")
        return candidate

    def info(self, name: str) -> dict[str, Any]:
        path = self.resolve(name)
        stat = path.stat()
        return {
            "name": name.replace("\\", "/"),
            "path": str(path),
            "size_bytes": stat.st_size,
            "size_gb": round(stat.st_size / (1024**3), 2),
        }

    def validate(self, name: str, *, minimum_size: int = 1024 * 1024) -> tuple[bool, str | None]:
        try:
            if not name.lower().endswith(".gguf"):
                return False, f"Model must be a .gguf file: {name}"
            path = self.resolve(name)
            if path.stat().st_size < minimum_size:
                return False, f"Model file appears too small ({path.stat().st_size} bytes): {name}"
            return True, None
        except (ModelCatalogError, OSError) as exc:
            return False, str(exc)
