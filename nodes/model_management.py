"""
Model Management Nodes
Nodes for listing, loading, and unloading models in router mode.
"""

import json
from ..server_manager import get_server_manager


class LlamaCppListModels:
    """
    ComfyUI node that lists available models from the server.
    Works in both single-model and router mode.
    """

    CATEGORY = "LlamaCpp"
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("models_json", "models_list")
    FUNCTION = "list_models"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {},
            "optional": {
                "trigger": ("*", {
                    "tooltip": "Optional input to trigger this node in a workflow"
                }),
            }
        }

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        # Always re-check
        return float("nan")

    def list_models(self, trigger=None):
        """List available models from the server"""

        manager = get_server_manager()

        if not manager.is_running:
            error_msg = "Error: No server running"
            print(f"[LlamaCpp] {error_msg}")
            return (error_msg, error_msg)

        success, models, error = manager.list_models()

        if not success:
            print(f"[LlamaCpp] {error}")
            return (error, error)

        # Format output
        models_json = json.dumps(models, indent=2)

        # Create simple list of model names/ids
        model_names = []
        for m in models:
            if isinstance(m, dict):
                name = m.get("id") or m.get("name") or m.get("model") or str(m)
                status = m.get("state", "unknown")
                model_names.append(f"{name} ({status})")
            else:
                model_names.append(str(m))

        models_list = "\n".join(model_names) if model_names else "No models found"

        print(f"[LlamaCpp] Found {len(models)} models")
        return (models_json, models_list)


class LlamaCppLoadModel:
    """
    ComfyUI node that loads a model in router mode.
    """

    CATEGORY = "LlamaCpp"
    RETURN_TYPES = ("BOOLEAN", "STRING")
    RETURN_NAMES = ("success", "message")
    FUNCTION = "load_model"
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model_name": ("STRING", {
                    "default": "",
                    "placeholder": "model.gguf",
                    "tooltip": "Name of the model to load"
                }),
            },
            "optional": {
                "trigger": ("*", {
                    "tooltip": "Optional input to trigger this node in a workflow"
                }),
            }
        }

    def load_model(self, model_name: str, trigger=None):
        """Load a model in router mode"""

        if not model_name.strip():
            message = "Error: No model name specified"
            print(f"[LlamaCpp] {message}")
            return (False, message)

        manager = get_server_manager()

        if not manager.is_running:
            message = "Error: No server running"
            print(f"[LlamaCpp] {message}")
            return (False, message)

        if not manager.is_router_mode:
            message = "Error: Server not in router mode. Use 'Start LlamaCpp Router' first."
            print(f"[LlamaCpp] {message}")
            return (False, message)

        success, error = manager.load_model(model_name.strip())

        if success:
            message = f"Model loaded: {model_name}"
            print(f"[LlamaCpp] {message}")
            return (True, message)
        else:
            print(f"[LlamaCpp] {error}")
            return (False, error)


class LlamaCppUnloadModel:
    """
    ComfyUI node that unloads a model in router mode to free VRAM.
    """

    CATEGORY = "LlamaCpp"
    RETURN_TYPES = ("BOOLEAN", "STRING")
    RETURN_NAMES = ("success", "message")
    FUNCTION = "unload_model"
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model_name": ("STRING", {
                    "default": "",
                    "placeholder": "model.gguf",
                    "tooltip": "Name of the model to unload"
                }),
            },
            "optional": {
                "trigger": ("*", {
                    "tooltip": "Optional input to trigger this node in a workflow"
                }),
            }
        }

    def unload_model(self, model_name: str, trigger=None):
        """Unload a model in router mode"""

        if not model_name.strip():
            message = "Error: No model name specified"
            print(f"[LlamaCpp] {message}")
            return (False, message)

        manager = get_server_manager()

        if not manager.is_running:
            message = "Error: No server running"
            print(f"[LlamaCpp] {message}")
            return (False, message)

        if not manager.is_router_mode:
            message = "Error: Server not in router mode"
            print(f"[LlamaCpp] {message}")
            return (False, message)

        success, error = manager.unload_model(model_name.strip())

        if success:
            message = f"Model unloaded: {model_name}"
            print(f"[LlamaCpp] {message}")
            return (True, message)
        else:
            print(f"[LlamaCpp] {error}")
            return (False, error)


NODE_CLASS_MAPPINGS = {
    "LlamaCppListModels": LlamaCppListModels,
    "LlamaCppLoadModel": LlamaCppLoadModel,
    "LlamaCppUnloadModel": LlamaCppUnloadModel,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "LlamaCppListModels": "LlamaCpp List Models",
    "LlamaCppLoadModel": "LlamaCpp Load Model",
    "LlamaCppUnloadModel": "LlamaCpp Unload Model",
}
