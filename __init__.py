"""
ComfyUI LlamaCpp Suite
A modular llama.cpp integration for ComfyUI.

Provides nodes for:
- Starting/stopping the llama-server
- Server status monitoring
- (Future: Chat completion, embeddings, etc.)
"""

__version__ = "0.1.0"

from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

__all__ = ['NODE_CLASS_MAPPINGS', 'NODE_DISPLAY_NAME_MAPPINGS']

# Print initialization message
print(f"[LlamaCpp] ComfyUI LlamaCpp Suite v{__version__} loaded")
print(f"[LlamaCpp] Registered {len(NODE_CLASS_MAPPINGS)} nodes: {', '.join(NODE_CLASS_MAPPINGS.keys())}")
