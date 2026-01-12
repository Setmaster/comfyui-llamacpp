"""
Model Manager
Handles discovering and managing GGUF model files.
"""

import os
import glob
from typing import List, Optional


def get_comfyui_root() -> str:
    """Get the ComfyUI root directory"""
    # Go up from custom_nodes/comfyui-llamacpp to ComfyUI root
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..")
    )


def get_models_directory() -> str:
    """
    Get the path to the models directory (ComfyUI/models/LLM/gguf).
    Creates the directory if it doesn't exist.
    """
    comfyui_root = get_comfyui_root()
    models_dir = os.path.join(comfyui_root, "models", "LLM", "gguf")
    
    # Create directory if it doesn't exist
    os.makedirs(models_dir, exist_ok=True)
    
    return models_dir


def get_local_models() -> List[str]:
    """
    Get list of local .gguf model files (excluding mmproj files).
    Returns filenames only, not full paths.
    """
    models_dir = get_models_directory()
    gguf_files = glob.glob(os.path.join(models_dir, "*.gguf"))

    # Return just filenames, sorted (excluding mmproj files)
    return sorted([os.path.basename(f) for f in gguf_files
                   if 'mmproj' not in os.path.basename(f).lower()])


def get_local_mmproj() -> List[str]:
    """
    Get list of local mmproj .gguf files (multimodal projectors for VLM).
    Returns filenames only, not full paths.
    """
    models_dir = get_models_directory()
    gguf_files = glob.glob(os.path.join(models_dir, "*.gguf"))

    # Return only mmproj files, sorted
    return sorted([os.path.basename(f) for f in gguf_files
                   if 'mmproj' in os.path.basename(f).lower()])


def get_model_path(model_name: str) -> str:
    """Get the full path to a model file"""
    return os.path.join(get_models_directory(), model_name)


def is_model_local(model_name: str) -> bool:
    """Check if a model exists locally"""
    model_path = get_model_path(model_name)
    return os.path.exists(model_path)


def get_model_info(model_name: str) -> Optional[dict]:
    """
    Get basic info about a model file.
    Returns None if model doesn't exist.
    """
    model_path = get_model_path(model_name)
    
    if not os.path.exists(model_path):
        return None
    
    stat = os.stat(model_path)
    size_gb = stat.st_size / (1024 ** 3)
    
    return {
        "name": model_name,
        "path": model_path,
        "size_bytes": stat.st_size,
        "size_gb": round(size_gb, 2),
    }


def validate_model(model_name: str) -> tuple[bool, Optional[str]]:
    """
    Validate that a model exists and appears to be a valid GGUF file.
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not model_name:
        return (False, "No model specified")
    
    model_path = get_model_path(model_name)
    
    if not os.path.exists(model_path):
        models_dir = get_models_directory()
        return (False, f"Model not found: {model_name}\nExpected location: {models_dir}")
    
    if not model_name.lower().endswith('.gguf'):
        return (False, f"Model must be a .gguf file: {model_name}")
    
    # Check file size (GGUF files should be at least a few MB)
    size = os.path.getsize(model_path)
    if size < 1024 * 1024:  # Less than 1MB
        return (False, f"Model file appears too small ({size} bytes): {model_name}")
    
    return (True, None)
