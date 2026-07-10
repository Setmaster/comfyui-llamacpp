"""
ComfyUI llama.cpp Suite
A modular llama.cpp integration for ComfyUI.

Provides nodes for:
- Starting/stopping the llama-server (single model and router mode)
- Server status monitoring
- Basic prompt with thinking mode support
- Model management (list, load, unload)
"""

if __package__:
    from ._version import __version__
else:  # direct tooling import, see below
    __version__ = "0.3.0"

# Pytest discovers the repository root as ``__init__`` because the checkout
# directory contains a hyphen. ComfyUI always imports this file as a package.
# Keep direct tooling imports side-effect free while preserving normal ComfyUI
# registration behavior.
if __package__:
    from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS
    from .runtime.comfy_bridge import install_comfy_bridge

    COMFY_BRIDGE_STATUS = install_comfy_bridge()
else:  # pragma: no cover - collection/bootstrap compatibility
    NODE_CLASS_MAPPINGS = {}
    NODE_DISPLAY_NAME_MAPPINGS = {}
    COMFY_BRIDGE_STATUS = None

# Web directory for frontend extensions
WEB_DIRECTORY = "./web"

__all__ = [
    "COMFY_BRIDGE_STATUS",
    "NODE_CLASS_MAPPINGS",
    "NODE_DISPLAY_NAME_MAPPINGS",
    "WEB_DIRECTORY",
]

# Print initialization message
print(f"[llama.cpp] ComfyUI llama.cpp Suite v{__version__} loaded")
print(
    f"[llama.cpp] Registered {len(NODE_CLASS_MAPPINGS)} nodes: {', '.join(NODE_CLASS_MAPPINGS.keys())}"
)
