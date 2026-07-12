"""Router model listing and terminal load/unload barrier nodes."""

from __future__ import annotations

import json

from ..model_manager import get_local_models
from ..server_manager import get_server_manager
from .presentation import NODE_CATEGORIES, NODE_SEARCH_ALIASES, apply_input_presentation


class LlamaCppListModels:
    DESCRIPTION = (
        "Lists the current llama-server model catalog and normalized router residency states."
    )
    CATEGORY = NODE_CATEGORIES["LlamaCppListModels"]
    SEARCH_ALIASES = NODE_SEARCH_ALIASES["LlamaCppListModels"]
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("models_json", "models_list")
    OUTPUT_TOOLTIPS = (
        "Complete router model catalog as formatted JSON.",
        "Human-readable model identities and residency states.",
    )
    FUNCTION = "list_models"

    @classmethod
    def INPUT_TYPES(cls):
        schema = {
            "required": {},
            "optional": {
                "trigger": (
                    "*",
                    {"tooltip": "Optional dependency input used to sequence model listing."},
                ),
                "reload_catalog": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "tooltip": (
                            "Ask a current llama-server router to rescan its configured "
                            "model sources before listing. Changed or removed running models "
                            "may be unloaded by the router. Reload waits for an idle managed "
                            "runtime and serializes with lifecycle operations."
                        ),
                    },
                ),
            },
        }
        return apply_input_presentation("LlamaCppListModels", schema)

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def list_models(self, trigger=None, reload_catalog: bool = False):
        del trigger
        success, models, error = get_server_manager().list_models(reload=reload_catalog)
        if not success or models is None:
            message = f"Error: {error or 'Could not list models'}"
            return message, message
        lines = []
        for model in models:
            model_id = model.get("id") or model.get("model") or model.get("name") or "unknown"
            status = model.get("status", model.get("state", "unknown"))
            if isinstance(status, dict):
                status = status.get("value", "unknown")
            lines.append(f"{model_id} ({status})")
        return json.dumps(models, indent=2), "\n".join(lines) if lines else "No models found"


def _model_choices() -> list[str]:
    return get_local_models() or ["No models found - add .gguf files to models/LLM/gguf/"]


class LlamaCppLoadModel:
    DESCRIPTION = "Loads one exact router model and waits for its terminal loaded state."
    CATEGORY = NODE_CATEGORIES["LlamaCppLoadModel"]
    SEARCH_ALIASES = NODE_SEARCH_ALIASES["LlamaCppLoadModel"]
    RETURN_TYPES = ("BOOLEAN", "STRING")
    RETURN_NAMES = ("success", "message")
    OUTPUT_TOOLTIPS = (
        "Whether the model reached a terminal loaded state.",
        "Load result or failure detail.",
    )
    FUNCTION = "load_model"
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        models = _model_choices()
        schema = {
            "required": {
                "model_name": (
                    models,
                    {
                        "default": models[0],
                        "tooltip": (
                            "Local model to resolve against the active router catalog. "
                            "Use List Models to confirm the authoritative ID and residency."
                        ),
                    },
                )
            },
            "optional": {
                "trigger": (
                    "*",
                    {"tooltip": "Optional dependency input used to sequence model loading."},
                ),
                "operation_timeout": (
                    "INT",
                    {
                        "default": 300,
                        "min": 1,
                        "max": 86400,
                        "tooltip": "Seconds to wait for the router's terminal loaded state.",
                    },
                ),
            },
        }
        return apply_input_presentation("LlamaCppLoadModel", schema)

    def load_model(self, model_name: str, trigger=None, operation_timeout: int = 300):
        del trigger
        success, error = get_server_manager().load_model(model_name.strip(), operation_timeout)
        message = f"Model loaded: {model_name}" if success else f"Load failed: {error}"
        return success, message


class LlamaCppUnloadModel:
    DESCRIPTION = "Unloads one exact router model and waits for its terminal unloaded state."
    CATEGORY = NODE_CATEGORIES["LlamaCppUnloadModel"]
    SEARCH_ALIASES = NODE_SEARCH_ALIASES["LlamaCppUnloadModel"]
    RETURN_TYPES = ("BOOLEAN", "STRING")
    RETURN_NAMES = ("success", "message")
    OUTPUT_TOOLTIPS = (
        "Whether the model reached a terminal unloaded state.",
        "Unload result or failure detail.",
    )
    FUNCTION = "unload_model"
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        models = _model_choices()
        schema = {
            "required": {
                "model_name": (
                    models,
                    {
                        "default": models[0],
                        "tooltip": (
                            "Local model to resolve against the active router catalog. "
                            "Use List Models to confirm the authoritative ID and residency."
                        ),
                    },
                )
            },
            "optional": {
                "trigger": (
                    "*",
                    {"tooltip": "Optional dependency input used to sequence model unloading."},
                ),
                "operation_timeout": (
                    "INT",
                    {
                        "default": 300,
                        "min": 1,
                        "max": 86400,
                        "tooltip": "Seconds to wait for the router's terminal unloaded state.",
                    },
                ),
            },
        }
        return apply_input_presentation("LlamaCppUnloadModel", schema)

    def unload_model(self, model_name: str, trigger=None, operation_timeout: int = 300):
        del trigger
        success, error = get_server_manager().unload_model(model_name.strip(), operation_timeout)
        message = f"Model unloaded: {model_name}" if success else f"Unload failed: {error}"
        return success, message


NODE_CLASS_MAPPINGS = {
    "LlamaCppListModels": LlamaCppListModels,
    "LlamaCppLoadModel": LlamaCppLoadModel,
    "LlamaCppUnloadModel": LlamaCppUnloadModel,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "LlamaCppListModels": "llama.cpp List Models",
    "LlamaCppLoadModel": "llama.cpp Load Model",
    "LlamaCppUnloadModel": "llama.cpp Unload Model",
}
