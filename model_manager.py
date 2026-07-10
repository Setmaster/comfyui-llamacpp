"""Backward-compatible facade for GGUF discovery and router identity."""

from __future__ import annotations

from .models.catalog import ModelCatalog, ModelCatalogError
from .models.catalog import get_comfyui_root as _root
from .models.identity import RouterIdentityError, resolve_router_model


def _catalog() -> ModelCatalog:
    return ModelCatalog()


def get_comfyui_root() -> str:
    return str(_root())


def get_models_directory() -> str:
    """Return and create the primary `models/LLM/gguf` directory."""

    return str(_catalog().ensure_default_root())


def get_local_models() -> list[str]:
    return _catalog().list_models()


def get_local_mmproj() -> list[str]:
    return _catalog().list_mmproj()


def get_model_path(model_name: str) -> str:
    """Resolve an existing model or return its safe primary-folder candidate."""

    catalog = _catalog()
    try:
        return str(catalog.resolve(model_name))
    except ModelCatalogError:
        return str(catalog.candidate(model_name))


def is_model_local(model_name: str) -> bool:
    try:
        return _catalog().resolve(model_name).is_file()
    except ModelCatalogError:
        return False


def get_model_info(model_name: str) -> dict | None:
    try:
        return _catalog().info(model_name)
    except (ModelCatalogError, OSError):
        return None


def validate_model(model_name: str) -> tuple[bool, str | None]:
    if not model_name:
        return False, "No model specified"
    valid, error = _catalog().validate(model_name)
    if not valid and error and "not found" in error.lower():
        error = f"{error}\nExpected folder: {get_models_directory()}"
    return valid, error


__all__ = [
    "ModelCatalogError",
    "RouterIdentityError",
    "get_comfyui_root",
    "get_local_mmproj",
    "get_local_models",
    "get_model_info",
    "get_model_path",
    "get_models_directory",
    "is_model_local",
    "resolve_router_model",
    "validate_model",
]
