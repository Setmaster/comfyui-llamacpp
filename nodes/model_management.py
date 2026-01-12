"""
Model Management Nodes
Nodes for listing, loading, and unloading models in router mode.
"""

import json
from ..server_manager import get_server_manager
from ..model_manager import get_local_models


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
            print(f"[llama.cpp] {error_msg}")
            return (error_msg, error_msg)

        success, models, error = manager.list_models()

        if not success:
            print(f"[llama.cpp] {error}")
            return (error, error)

        # Format output
        models_json = json.dumps(models, indent=2)

        # Create simple list of model names/ids
        model_names = []
        for m in models:
            if isinstance(m, dict):
                model_id = m.get("id") or m.get("name") or m.get("model") or str(m)
                # Status can be a string or a nested object with "value" key
                status_raw = m.get("state") or m.get("status") or "unknown"
                if isinstance(status_raw, dict):
                    status = status_raw.get("value", "unknown")
                else:
                    status = status_raw
                model_names.append(f"{model_id} ({status})")
                print(f"[llama.cpp] Model: id='{model_id}' status={status}")
            else:
                model_names.append(str(m))

        models_list = "\n".join(model_names) if model_names else "No models found"

        print(f"[llama.cpp] Found {len(models)} models")
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
        # Get available models for dropdown (already excludes mmproj files)
        local_models = get_local_models()

        if not local_models:
            local_models = ["No models found - add .gguf files to models/LLM/gguf/"]

        return {
            "required": {
                "model_name": (local_models, {
                    "default": local_models[0] if local_models else "",
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
            print(f"[llama.cpp] {message}")
            return (False, message)

        manager = get_server_manager()

        if not manager.is_running:
            message = "Error: No server running"
            print(f"[llama.cpp] {message}")
            return (False, message)

        if not manager.is_router_mode:
            message = "Error: Server not in router mode. Use 'Start llama.cpp Router' first."
            print(f"[llama.cpp] {message}")
            return (False, message)

        # Router uses model IDs without .gguf extension
        clean_name = model_name.strip()
        if clean_name.lower().endswith('.gguf'):
            clean_name = clean_name[:-5]

        success, error = manager.load_model(clean_name)

        if success:
            message = f"Model loaded: {model_name}"
            print(f"[llama.cpp] {message}")
            return (True, message)
        else:
            print(f"[llama.cpp] {error}")
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
        # Get available models for dropdown (already excludes mmproj files)
        local_models = get_local_models()

        if not local_models:
            local_models = ["No models found - add .gguf files to models/LLM/gguf/"]

        return {
            "required": {
                "model_name": (local_models, {
                    "default": local_models[0] if local_models else "",
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
            print(f"[llama.cpp] {message}")
            return (False, message)

        manager = get_server_manager()

        if not manager.is_running:
            message = "Error: No server running"
            print(f"[llama.cpp] {message}")
            return (False, message)

        if not manager.is_router_mode:
            message = "Error: Server not in router mode"
            print(f"[llama.cpp] {message}")
            return (False, message)

        # Router uses model IDs without .gguf extension
        clean_name = model_name.strip()
        if clean_name.lower().endswith('.gguf'):
            clean_name = clean_name[:-5]

        success, error = manager.unload_model(clean_name)

        if success:
            message = f"Model unloaded: {model_name}"
            print(f"[llama.cpp] {message}")
            return (True, message)
        else:
            print(f"[llama.cpp] {error}")
            return (False, error)


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
