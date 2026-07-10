"""Start a positively owned single-model llama-server."""

from ..model_manager import (
    get_local_mmproj,
    get_local_models,
    get_model_path,
    validate_model,
)
from ..server_manager import ServerConfig, get_server_manager
from .server_utils import (
    optional_path,
    parse_extra_args,
    parse_gpu_layers,
    parse_threads,
    parse_timeout,
)


class StartLlamaCppServer:
    CATEGORY = "LlamaCpp"
    RETURN_TYPES = ("STRING", "BOOLEAN")
    RETURN_NAMES = ("server_url", "success")
    FUNCTION = "start_server"

    @classmethod
    def INPUT_TYPES(cls):
        models = get_local_models() or ["No models found - add .gguf files to models/LLM/gguf/"]
        mmproj = ["(auto)", *get_local_mmproj()]
        return {
            "required": {
                "model": (models, {"default": models[0]}),
                "context_size": (
                    "INT",
                    {"default": 4096, "min": 0, "max": 1048576, "step": 256},
                ),
                "gpu_layers": (
                    "STRING",
                    {
                        "default": "",
                        "placeholder": "empty = legacy all, or auto/all/number",
                    },
                ),
                "main_gpu": ("INT", {"default": 0, "min": 0, "max": 31, "step": 1}),
            },
            "optional": {
                # Released v0.2.1 prefix. Append only after timeout.
                "port": ("INT", {"default": 8080, "min": 1, "max": 65535}),
                "threads": ("STRING", {"default": "", "placeholder": "auto"}),
                "batch_size": (
                    "INT",
                    {"default": 512, "min": 1, "max": 65536, "step": 1},
                ),
                "flash_attention": ("BOOLEAN", {"default": False}),
                "timeout": ("STRING", {"default": "60", "placeholder": "empty = no limit"}),
                "binary_path": (
                    "STRING",
                    {
                        "default": "",
                        "placeholder": "empty = LLAMA_SERVER_BINARY or PATH",
                    },
                ),
                "host": ("STRING", {"default": "127.0.0.1"}),
                "tensor_split": ("STRING", {"default": ""}),
                "no_mmap": ("BOOLEAN", {"default": False}),
                "flash_attention_mode": (
                    ["legacy", "auto", "on", "off"],
                    {"default": "legacy"},
                ),
                "mmproj": (mmproj, {"default": "(auto)"}),
                "sleep_idle_seconds": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 86400,
                        "tooltip": "0 disables idle sleep. Positive values release model VRAM while idle.",
                    },
                ),
                "api_key_file": (
                    "STRING",
                    {"default": "", "tooltip": "Path to a llama-server API key file."},
                ),
                "api_key_env": (
                    "STRING",
                    {
                        "default": "LLAMACPP_API_KEY",
                        "tooltip": "Environment variable containing the matching client key.",
                    },
                ),
                "media_path": ("STRING", {"default": ""}),
                "fit_mode": (
                    ["upstream default", "on", "off"],
                    {"default": "upstream default"},
                ),
                "unload_comfy_models_before_start": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "tooltip": "Evict Comfy-managed models before llama-server allocates GPU memory.",
                    },
                ),
                "extra_args": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": "",
                        "tooltip": "Advanced llama-server arguments. No shell is used.",
                    },
                ),
            },
        }

    @classmethod
    def IS_CHANGED(cls, **kwargs):
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
        binary_path: str = "",
        host: str = "127.0.0.1",
        tensor_split: str = "",
        no_mmap: bool = False,
        flash_attention_mode: str = "legacy",
        mmproj: str = "(auto)",
        sleep_idle_seconds: int = 0,
        api_key_file: str = "",
        api_key_env: str = "LLAMACPP_API_KEY",
        media_path: str = "",
        fit_mode: str = "upstream default",
        unload_comfy_models_before_start: bool = False,
        extra_args: str = "",
    ):
        valid, error = validate_model(model)
        if not valid:
            return (error or "Invalid model", False)

        mmproj_path = None
        if mmproj and mmproj != "(auto)":
            mmproj_path = get_model_path(mmproj)
        modern_flash = None if flash_attention_mode == "legacy" else flash_attention_mode
        fit = None if fit_mode == "upstream default" else fit_mode == "on"
        try:
            config = ServerConfig(
                model_path=get_model_path(model),
                port=port,
                host=host.strip() or "127.0.0.1",
                context_size=context_size,
                n_gpu_layers=parse_gpu_layers(gpu_layers),
                main_gpu=main_gpu,
                tensor_split=tensor_split.strip() or None,
                threads=parse_threads(threads),
                batch_size=batch_size,
                flash_attention=flash_attention if modern_flash is None else False,
                no_mmap=no_mmap,
                mmproj_path=mmproj_path,
                sleep_idle_seconds=sleep_idle_seconds or None,
                api_key_file=optional_path(api_key_file),
                media_path=optional_path(media_path),
                flash_attention_mode=modern_flash,
                fit=fit,
                extra_args=parse_extra_args(extra_args),
            )
        except ValueError as exc:
            return (f"Invalid server configuration: {exc}", False)

        manager = get_server_manager()
        success, error = manager.start(
            config,
            timeout=parse_timeout(timeout),
            binary_path=optional_path(binary_path),
            api_key_env=api_key_env,
            unload_comfy_models_before_start=unload_comfy_models_before_start,
        )
        return (manager.server_url, True) if success else (error or "Server start failed", False)


NODE_CLASS_MAPPINGS = {"StartLlamaCppServer": StartLlamaCppServer}
NODE_DISPLAY_NAME_MAPPINGS = {"StartLlamaCppServer": "Start llama.cpp Server"}
