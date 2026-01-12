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
                "n_gpu_layers": ("INT", {
                    "default": 999,
                    "min": 0,
                    "max": 999,
                    "step": 1,
                    "tooltip": "Number of layers to offload to GPU. 999 = all layers."
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
                "threads": ("INT", {
                    "default": 0,
                    "min": 0,
                    "max": 128,
                    "step": 1,
                    "tooltip": "Number of CPU threads (0 = auto)"
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
                "timeout": ("INT", {
                    "default": 60,
                    "min": 10,
                    "max": 300,
                    "step": 10,
                    "tooltip": "Server startup timeout in seconds"
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
        n_gpu_layers: int,
        main_gpu: int,
        port: int = 8080,
        threads: int = 0,
        batch_size: int = 512,
        flash_attention: bool = False,
        timeout: int = 60,
    ):
        """Start the llama-server with the specified configuration"""
        
        # Validate model
        is_valid, error = validate_model(model)
        if not is_valid:
            print(f"[LlamaCpp] {error}")
            return ("", False)
        
        # Get model path
        model_path = get_model_path(model)
        
        # Create server config
        config = ServerConfig(
            model_path=model_path,
            port=port,
            context_size=context_size,
            n_gpu_layers=n_gpu_layers,
            main_gpu=main_gpu,
            threads=threads if threads > 0 else None,
            batch_size=batch_size,
            flash_attention=flash_attention,
        )
        
        # Get server manager and start
        manager = get_server_manager()
        success, error = manager.start(config, timeout=timeout)
        
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
