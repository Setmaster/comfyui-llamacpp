"""Comfy-aware, containment-safe GGUF model catalog."""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FOLDER_KEY = "llamacpp"


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


def register_model_folder() -> None:
    folder_paths = _folder_paths_module()
    if folder_paths is None:
        return
    default = str(get_default_models_directory())
    try:
        folder_paths.add_model_folder_path(FOLDER_KEY, default, is_default=True)
    except TypeError:
        folder_paths.add_model_folder_path(FOLDER_KEY, default)


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
        configured = roots if roots is not None else _configured_roots()
        self.roots = tuple(dict.fromkeys(Path(root).expanduser().resolve() for root in configured))

    @property
    def default_root(self) -> Path:
        return self.roots[0] if self.roots else get_default_models_directory()

    def ensure_default_root(self) -> Path:
        self.default_root.mkdir(parents=True, exist_ok=True)
        return self.default_root

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
