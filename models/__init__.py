"""GGUF discovery and llama-server model identity helpers."""

from .catalog import ModelCatalog, ModelCatalogError, get_default_models_directory
from .identity import RouterIdentityError, resolve_router_model
from .projectors import (
    AUTO_PROJECTOR,
    NONE_PROJECTOR,
    ProjectorResolution,
    ProjectorResolutionError,
    resolve_direct_projector,
)

__all__ = [
    "AUTO_PROJECTOR",
    "ModelCatalog",
    "ModelCatalogError",
    "NONE_PROJECTOR",
    "ProjectorResolution",
    "ProjectorResolutionError",
    "RouterIdentityError",
    "get_default_models_directory",
    "resolve_direct_projector",
    "resolve_router_model",
]
