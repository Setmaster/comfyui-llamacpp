"""
Start LlamaCpp Server Node
Starts the llama-server with specified configuration.
"""

from ..server_manager import get_server_manager, ServerConfig
from ..model_manager import get_local_models, get_model_path, validate_model


class StartLlamaCppServer:
    """
    ComfyUI node that starts the llama.cpp server.
    Skips starting if server is already running with the same configuration.
    """
    
    CATEGORY = "LlamaCpp"
    RETURN_TYPES = ("STRING", "BOOLEAN")
    RETURN_NAMES = ("server_url", "success")
    FUNCTION = "start_server"
    
    @classmethod
    def INPUT_TYPES(cls):
        local_models = get_local_models()
        
        # Filter out mmproj files (vision model projectors)
        local_models = [m for m in local_models if 'mmproj' not in m.lower()]
        
        if not local_models:
            local_models = ["No models found - add .gguf files to models/LLM/gguf/"]
        
        return {
            "required": {
                "model": (local_models, {
                    "default": local_models[0] if local_models else "",
                    "tooltip": "Select the GGUF model to load"
                }),
                "context_size": ("INT", {
                    "default": 4096,
                    "min": 512,
                    "max": 131072,
                    "step": 256,
                    "tooltip": "Context window size (total tokens). Higher = more memory usage."
                }),
                "gpu_layers": ("STRING", {
                    "default": "",
                    "placeholder": "empty = all, or number (e.g. 32)",
                    "tooltip": "Layers to offload to GPU. Empty = all layers, 0 = CPU only, or specify a number."
                }),
                "main_gpu": ("INT", {
                    "default": 0,
                    "min": 0,
                    "max": 7,
                    "step": 1,
                    "tooltip": "Main GPU to use (for multi-GPU systems)"
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
    
    def start_server(
        self,
        model: str,
        context_size: int,
        gpu_layers: str,
        main_gpu: int,
        port: int = 8080,
        threads: str = "",
        batch_size: int = 512,
        flash_attention: bool = False,
        timeout: str = "60",
    ):
        """Start the llama-server with the specified configuration"""
        
        # Validate model
        is_valid, error = validate_model(model)
        if not is_valid:
            print(f"[LlamaCpp] {error}")
            return ("", False)
        
        # Get model path
        model_path = get_model_path(model)
        
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

        # Create server config
        config = ServerConfig(
            model_path=model_path,
            port=port,
            context_size=context_size,
            n_gpu_layers=n_gpu_layers,
            main_gpu=main_gpu,
            threads=n_threads,
            batch_size=batch_size,
            flash_attention=flash_attention,
        )

        # Get server manager and start
        manager = get_server_manager()
        success, error = manager.start(config, timeout=timeout_seconds)
        
        if success:
            server_url = manager.server_url
            print(f"[LlamaCpp] Server running at: {server_url}")
            return (server_url, True)
        else:
            print(f"[LlamaCpp] Failed to start server: {error}")
            return (error or "Unknown error", False)


NODE_CLASS_MAPPINGS = {
    "StartLlamaCppServer": StartLlamaCppServer
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "StartLlamaCppServer": "Start LlamaCpp Server"
}
