"""
Start LlamaCpp Router Node
Starts the llama-server in router mode for multi-model support.
"""

from ..server_manager import get_server_manager, RouterConfig
from ..model_manager import get_models_directory


class StartLlamaCppRouter:
    """
    ComfyUI node that starts the llama.cpp server in router mode.
    Router mode allows dynamic loading/unloading of multiple models.
    """

    CATEGORY = "LlamaCpp"
    RETURN_TYPES = ("STRING", "BOOLEAN")
    RETURN_NAMES = ("server_url", "success")
    FUNCTION = "start_router"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "context_size": ("INT", {
                    "default": 4096,
                    "min": 512,
                    "max": 131072,
                    "step": 256,
                    "tooltip": "Context window size (tokens). Applied to all loaded models."
                }),
                "gpu_layers": ("STRING", {
                    "default": "",
                    "placeholder": "empty = all, or number (e.g. 32)",
                    "tooltip": "Layers to offload to GPU. Empty = all layers, 0 = CPU only."
                }),
                "main_gpu": ("INT", {
                    "default": 0,
                    "min": 0,
                    "max": 7,
                    "step": 1,
                    "tooltip": "Main GPU to use (for multi-GPU systems)"
                }),
                "models_max": ("INT", {
                    "default": 4,
                    "min": 1,
                    "max": 16,
                    "step": 1,
                    "tooltip": "Maximum number of models loaded simultaneously. Uses LRU eviction."
                }),
            },
            "optional": {
                "port": ("INT", {
                    "default": 8080,
                    "min": 1024,
                    "max": 65535,
                    "step": 1,
                    "tooltip": "Server port"
                }),
                "threads": ("STRING", {
                    "default": "",
                    "placeholder": "auto",
                    "tooltip": "Number of CPU threads. Empty = auto."
                }),
                "batch_size": ("INT", {
                    "default": 512,
                    "min": 1,
                    "max": 8192,
                    "step": 1,
                    "tooltip": "Batch size for prompt processing"
                }),
                "flash_attention": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "Enable flash attention (faster, requires compatible GPU)"
                }),
                "models_autoload": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "Auto-load models on first request. Disable for manual control."
                }),
                "timeout": ("STRING", {
                    "default": "60",
                    "placeholder": "60",
                    "tooltip": "Server startup timeout in seconds. Empty = no timeout."
                }),
            }
        }

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        # Always re-check server status
        return float("nan")

    def start_router(
        self,
        context_size: int,
        gpu_layers: str,
        main_gpu: int,
        models_max: int,
        port: int = 8080,
        threads: str = "",
        batch_size: int = 512,
        flash_attention: bool = False,
        models_autoload: bool = True,
        timeout: str = "60",
    ):
        """Start the llama-server in router mode"""

        # Get models directory
        models_dir = get_models_directory()

        # Parse gpu_layers: empty string means all (999), otherwise parse as int
        gpu_layers_str = gpu_layers.strip()
        if gpu_layers_str == "":
            n_gpu_layers = 999  # All layers
        else:
            try:
                n_gpu_layers = int(gpu_layers_str)
            except ValueError:
                print(f"[LlamaCpp] Invalid gpu_layers value '{gpu_layers}', using all layers")
                n_gpu_layers = 999

        # Parse threads: empty string means auto (None)
        threads_str = threads.strip()
        if threads_str == "":
            n_threads = None  # Auto
        else:
            try:
                n_threads = int(threads_str)
                if n_threads <= 0:
                    n_threads = None
            except ValueError:
                print(f"[LlamaCpp] Invalid threads value '{threads}', using auto")
                n_threads = None

        # Parse timeout: empty string means no timeout (None)
        timeout_str = timeout.strip()
        if timeout_str == "":
            timeout_seconds = None  # No timeout
        else:
            try:
                timeout_seconds = int(timeout_str)
                if timeout_seconds <= 0:
                    timeout_seconds = None
            except ValueError:
                print(f"[LlamaCpp] Invalid timeout value '{timeout}', using 60 seconds")
                timeout_seconds = 60

        # Create router config
        config = RouterConfig(
            models_dir=models_dir,
            port=port,
            context_size=context_size,
            n_gpu_layers=n_gpu_layers,
            main_gpu=main_gpu,
            threads=n_threads,
            batch_size=batch_size,
            flash_attention=flash_attention,
            models_max=models_max,
            models_autoload=models_autoload,
        )

        # Get server manager and start router
        manager = get_server_manager()
        success, error = manager.start_router(config, timeout=timeout_seconds)

        if success:
            server_url = manager.server_url
            print(f"[LlamaCpp] Router running at: {server_url}")
            print(f"[LlamaCpp] Models directory: {models_dir}")
            print(f"[LlamaCpp] Max loaded models: {models_max}")
            return (server_url, True)
        else:
            print(f"[LlamaCpp] Failed to start router: {error}")
            return (error or "Unknown error", False)


NODE_CLASS_MAPPINGS = {
    "StartLlamaCppRouter": StartLlamaCppRouter
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "StartLlamaCppRouter": "Start LlamaCpp Router"
}
