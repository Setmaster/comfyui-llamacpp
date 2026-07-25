"""Start a positively owned single-model llama-server."""

from ..model_manager import (
    AUTO_PROJECTOR,
    NONE_PROJECTOR,
    get_local_mmproj,
    get_local_models,
    resolve_direct_projector,
    validate_model,
)
from ..server_manager import ServerConfig, get_server_manager
from .presentation import NODE_CATEGORIES, NODE_SEARCH_ALIASES, apply_input_presentation
from .server_utils import (
    optional_path,
    parse_extra_args,
    parse_gpu_layers,
    parse_threads,
    parse_timeout,
)


class StartLlamaCppServer:
    DESCRIPTION = (
        "Launches and owns a local single-model llama-server process with "
        "capability-checked modern options."
    )
    CATEGORY = NODE_CATEGORIES["StartLlamaCppServer"]
    SEARCH_ALIASES = NODE_SEARCH_ALIASES["StartLlamaCppServer"]
    RETURN_TYPES = ("STRING", "BOOLEAN")
    RETURN_NAMES = ("server_url", "success")
    OUTPUT_TOOLTIPS = (
        "URL of the running owned llama-server.",
        "Whether server startup reached its ready state.",
    )
    FUNCTION = "start_server"

    @classmethod
    def INPUT_TYPES(cls):
        models = get_local_models() or ["No models found - add .gguf files to models/LLM/gguf/"]
        mmproj = [AUTO_PROJECTOR, NONE_PROJECTOR, *get_local_mmproj()]
        schema = {
            "required": {
                "model": (
                    models,
                    {
                        "default": models[0],
                        "tooltip": "GGUF model file to serve from a configured Comfy model folder.",
                    },
                ),
                "context_size": (
                    "INT",
                    {
                        "default": 4096,
                        "min": 0,
                        "max": 1048576,
                        "step": 256,
                        "tooltip": "Maximum context size in tokens. 0 lets llama-server choose.",
                    },
                ),
                "gpu_layers": (
                    "STRING",
                    {
                        "default": "",
                        "placeholder": "empty = legacy all, or auto/all/number",
                        "tooltip": (
                            "Layers to offload to GPU. Empty preserves legacy all-layers "
                            "behavior; auto, all, or a number are also accepted."
                        ),
                    },
                ),
                "main_gpu": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 31,
                        "step": 1,
                        "tooltip": "Primary GPU index used by llama-server.",
                    },
                ),
            },
            "optional": {
                # Released v0.2.1 prefix. Append only after timeout.
                "port": (
                    "INT",
                    {
                        "default": 8080,
                        "min": 1,
                        "max": 65535,
                        "tooltip": "TCP port for the owned llama-server.",
                    },
                ),
                "threads": (
                    "STRING",
                    {
                        "default": "",
                        "placeholder": "auto",
                        "tooltip": "CPU generation threads. Empty lets llama-server choose.",
                    },
                ),
                "batch_size": (
                    "INT",
                    {
                        "default": 512,
                        "min": 1,
                        "max": 65536,
                        "step": 1,
                        "tooltip": "Logical prompt-processing batch size.",
                    },
                ),
                "flash_attention": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "tooltip": (
                            "Legacy flash-attention toggle. Prefer flash_attention_mode "
                            "for new workflows."
                        ),
                    },
                ),
                "timeout": (
                    "STRING",
                    {
                        "default": "60",
                        "placeholder": "empty = no limit",
                        "tooltip": "Startup readiness deadline in seconds. Empty has no limit.",
                    },
                ),
                "binary_path": (
                    "STRING",
                    {
                        "default": "",
                        "placeholder": "empty = environment or PATH",
                        "tooltip": (
                            "Explicit llama-server executable. Empty uses "
                            "LLAMA_SERVER_BINARY, LLAMA_CPP_SERVER, then PATH."
                        ),
                    },
                ),
                "host": (
                    "STRING",
                    {
                        "default": "127.0.0.1",
                        "tooltip": "Network interface address to bind. Loopback is safest.",
                    },
                ),
                "tensor_split": (
                    "STRING",
                    {
                        "default": "",
                        "tooltip": "Per-GPU model proportions, for example 3,1.",
                    },
                ),
                "no_mmap": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "tooltip": "Disable memory-mapped model loading.",
                    },
                ),
                "flash_attention_mode": (
                    ["legacy", "auto", "on", "off"],
                    {
                        "default": "legacy",
                        "tooltip": (
                            "Modern flash-attention mode. Legacy preserves the released "
                            "flash_attention widget behavior."
                        ),
                    },
                ),
                "mmproj": (
                    mmproj,
                    {
                        "default": AUTO_PROJECTOR,
                        "tooltip": (
                            "Automatically selects one confidently compatible local projector. "
                            "Text models start without one. A known vision model with no match, "
                            "or more than one distinct compatible projector identity, requires "
                            "a choice. Select "
                            f"{NONE_PROJECTOR} to disable vision. An explicit projector entry "
                            "uses that exact file."
                        ),
                    },
                ),
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
                    {
                        "default": "",
                        "tooltip": "Path to a llama-server API key file, one key per line.",
                    },
                ),
                "api_key_env": (
                    "STRING",
                    {
                        "default": "LLAMACPP_API_KEY",
                        "tooltip": (
                            "Environment variable containing the matching client key. "
                            "The secret is not serialized."
                        ),
                    },
                ),
                "media_path": (
                    "STRING",
                    {
                        "default": "",
                        "tooltip": "Directory allowed for llama-server file:// media inputs.",
                    },
                ),
                "fit_mode": (
                    ["upstream default", "on", "off"],
                    {
                        "default": "upstream default",
                        "tooltip": (
                            "Control whether llama-server adjusts unset arguments to fit "
                            "device memory."
                        ),
                    },
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
        return apply_input_presentation("StartLlamaCppServer", schema)

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
        mmproj: str = AUTO_PROJECTOR,
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

        resolution = resolve_direct_projector(model, mmproj)
        mmproj_path = (
            str(resolution.projector_path) if resolution.projector_path is not None else None
        )
        modern_flash = None if flash_attention_mode == "legacy" else flash_attention_mode
        fit = None if fit_mode == "upstream default" else fit_mode == "on"
        try:
            config = ServerConfig(
                model_path=str(resolution.model_path),
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
                no_mmproj=resolution.projector_path is None,
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
            projector_status=resolution.status_dict(),
        )
        return (manager.server_url, True) if success else (error or "Server start failed", False)


NODE_CLASS_MAPPINGS = {"StartLlamaCppServer": StartLlamaCppServer}
NODE_DISPLAY_NAME_MAPPINGS = {"StartLlamaCppServer": "Start llama.cpp Server"}
