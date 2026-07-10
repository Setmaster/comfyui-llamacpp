"""Router model listing and terminal load/unload barrier nodes."""

from __future__ import annotations

import json

from ..model_manager import get_local_models
from ..server_manager import get_server_manager


class LlamaCppListModels:
    CATEGORY = "LlamaCpp"
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("models_json", "models_list")
    FUNCTION = "list_models"

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {}, "optional": {"trigger": ("*", {})}}

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def list_models(self, trigger=None):
        del trigger
        success, models, error = get_server_manager().list_models()
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
    CATEGORY = "LlamaCpp"
    RETURN_TYPES = ("BOOLEAN", "STRING")
    RETURN_NAMES = ("success", "message")
    FUNCTION = "load_model"
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        models = _model_choices()
        return {
            "required": {"model_name": (models, {"default": models[0]})},
            "optional": {
                "trigger": ("*", {}),
                "operation_timeout": (
                    "INT",
                    {"default": 300, "min": 1, "max": 86400},
                ),
            },
        }

    def load_model(self, model_name: str, trigger=None, operation_timeout: int = 300):
        del trigger
        success, error = get_server_manager().load_model(model_name.strip(), operation_timeout)
        message = f"Model loaded: {model_name}" if success else f"Load failed: {error}"
        return success, message


class LlamaCppUnloadModel:
    CATEGORY = "LlamaCpp"
    RETURN_TYPES = ("BOOLEAN", "STRING")
    RETURN_NAMES = ("success", "message")
    FUNCTION = "unload_model"
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        models = _model_choices()
        return {
            "required": {"model_name": (models, {"default": models[0]})},
            "optional": {
                "trigger": ("*", {}),
                "operation_timeout": (
                    "INT",
                    {"default": 300, "min": 1, "max": 86400},
                ),
            },
        }

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
