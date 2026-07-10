"""Backward-compatible facade for GGUF discovery and router identity."""

from __future__ import annotations

from .models import catalog as catalog_module
from .models.catalog import ModelCatalog, ModelCatalogError
from .models.identity import RouterIdentityError, resolve_router_model


def _catalog() -> ModelCatalog:
    return ModelCatalog()


def get_comfyui_root() -> str:
    return str(catalog_module.get_comfyui_root())


def get_models_directory() -> str:
    """Return and create the primary `models/LLM/gguf` directory."""

    root = catalog_module.get_default_models_directory()
    root.mkdir(parents=True, exist_ok=True)
    return str(root)


def get_model_directories() -> list[str]:
    """Return all configured llama.cpp roots in ComfyUI priority order."""

    return [str(root) for root in _catalog().roots]


def get_router_models_directory(selection: str = "(auto)") -> str:
    """Resolve a configured root suitable for llama-server router discovery."""

    return str(_catalog().resolve_router_root(selection))


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
    "get_model_directories",
    "get_models_directory",
    "get_router_models_directory",
    "is_model_local",
    "resolve_router_model",
    "validate_model",
]
