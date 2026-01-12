"""
ComfyUI llama.cpp Suite
A modular llama.cpp integration for ComfyUI.

Provides nodes for:
- Starting/stopping the llama-server (single model and router mode)
- Server status monitoring
- Basic prompt with thinking mode support
- Model management (list, load, unload)
"""

__version__ = "0.2.0"

from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

# Web directory for frontend extensions
WEB_DIRECTORY = "./web"

__all__ = ['NODE_CLASS_MAPPINGS', 'NODE_DISPLAY_NAME_MAPPINGS', 'WEB_DIRECTORY']

# Print initialization message
print(f"[llama.cpp] ComfyUI llama.cpp Suite v{__version__} loaded")
print(f"[llama.cpp] Registered {len(NODE_CLASS_MAPPINGS)} nodes: {', '.join(NODE_CLASS_MAPPINGS.keys())}")
