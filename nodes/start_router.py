"""Start a positively owned multi-model llama-server router."""

from ..model_manager import get_model_directories, get_router_models_directory
from ..server_manager import RouterConfig, get_server_manager
from .presentation import NODE_CATEGORIES, NODE_SEARCH_ALIASES, apply_input_presentation
from .server_utils import (
    optional_path,
    parse_extra_args,
    parse_gpu_layers,
    parse_threads,
    parse_timeout,
)


class StartLlamaCppRouter:
    DESCRIPTION = (
        "Launches and owns llama-server in multi-model router mode for explicit, "
        "terminal model load and unload control."
    )
    CATEGORY = NODE_CATEGORIES["StartLlamaCppRouter"]
    SEARCH_ALIASES = NODE_SEARCH_ALIASES["StartLlamaCppRouter"]
    RETURN_TYPES = ("STRING", "BOOLEAN")
    RETURN_NAMES = ("server_url", "success")
    OUTPUT_TOOLTIPS = (
        "URL of the running owned llama-server router.",
        "Whether router startup reached its ready state.",
    )
    FUNCTION = "start_router"

    @classmethod
    def INPUT_TYPES(cls):
        router_roots = ["(auto)", *get_model_directories()]
        schema = {
            "required": {
                "context_size": (
                    "INT",
                    {
                        "default": 4096,
                        "min": 0,
                        "max": 1048576,
                        "step": 256,
                        "tooltip": "Maximum context size for routed models. 0 uses upstream logic.",
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
                        "tooltip": "Primary GPU index used by routed model instances.",
                    },
                ),
                "models_max": (
                    "INT",
                    {
                        "default": 4,
                        "min": 0,
                        "max": 128,
                        "tooltip": "Maximum model instances the router may keep loaded.",
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
                        "tooltip": "TCP port for the owned llama-server router.",
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
                        "tooltip": "Logical prompt-processing batch size for routed models.",
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
                "models_autoload": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "tooltip": "Load an unloaded router model automatically on first request.",
                    },
                ),
                "timeout": (
                    "STRING",
                    {
                        "default": "60",
                        "placeholder": "empty = no limit",
                        "tooltip": "Router startup readiness deadline. Empty has no limit.",
                    },
                ),
                "binary_path": (
                    "STRING",
                    {
                        "default": "",
                        "placeholder": "empty = environment or PATH",
                        "tooltip": (
                            "Explicit llama-server executable. Empty uses "
                            "LLAMA_SERVER_BINARY, then PATH."
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
                "sleep_idle_seconds": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 86400,
                        "tooltip": "Release each model's state after this idle period. 0 disables.",
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
                        "tooltip": "Evict Comfy-managed models before router startup.",
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
                "models_directory": (
                    router_roots,
                    {
                        "default": "(auto)",
                        "tooltip": (
                            "Configured GGUF root exposed to the router. Auto selects the "
                            "root with the most unambiguous models visible to llama-server's "
                            "one-level directory scan. Each bundle directory must contain "
                            "one base model and at most one projector."
                        ),
                    },
                ),
            },
        }
        return apply_input_presentation("StartLlamaCppRouter", schema)

    @classmethod
    def IS_CHANGED(cls, **kwargs):
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
        binary_path: str = "",
        host: str = "127.0.0.1",
        tensor_split: str = "",
        no_mmap: bool = False,
        flash_attention_mode: str = "legacy",
        sleep_idle_seconds: int = 0,
        api_key_file: str = "",
        api_key_env: str = "LLAMACPP_API_KEY",
        media_path: str = "",
        fit_mode: str = "upstream default",
        unload_comfy_models_before_start: bool = False,
        extra_args: str = "",
        models_directory: str = "(auto)",
    ):
        modern_flash = None if flash_attention_mode == "legacy" else flash_attention_mode
        fit = None if fit_mode == "upstream default" else fit_mode == "on"
        try:
            config = RouterConfig(
                models_dir=get_router_models_directory(models_directory),
                port=port,
                host=host.strip() or "127.0.0.1",
                context_size=context_size,
                n_gpu_layers=parse_gpu_layers(gpu_layers),
                main_gpu=main_gpu,
                threads=parse_threads(threads),
                batch_size=batch_size,
                flash_attention=flash_attention if modern_flash is None else False,
                models_max=models_max,
                models_autoload=models_autoload,
                tensor_split=tensor_split.strip() or None,
                no_mmap=no_mmap,
                sleep_idle_seconds=sleep_idle_seconds or None,
                api_key_file=optional_path(api_key_file),
                media_path=optional_path(media_path),
                flash_attention_mode=modern_flash,
                fit=fit,
                extra_args=parse_extra_args(extra_args),
            )
        except ValueError as exc:
            return (f"Invalid router configuration: {exc}", False)

        manager = get_server_manager()
        success, error = manager.start_router(
            config,
            timeout=parse_timeout(timeout),
            binary_path=optional_path(binary_path),
            api_key_env=api_key_env,
            unload_comfy_models_before_start=unload_comfy_models_before_start,
        )
        return (manager.server_url, True) if success else (error or "Router start failed", False)


NODE_CLASS_MAPPINGS = {"StartLlamaCppRouter": StartLlamaCppRouter}
NODE_DISPLAY_NAME_MAPPINGS = {"StartLlamaCppRouter": "Start llama.cpp Router"}
