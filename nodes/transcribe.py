"""One bounded experimental AUDIO-to-text operation on an owned direct server."""

from __future__ import annotations

from typing import Any

from ..generation.audio import SUPPORTED_ASR_PAIR
from ..generation.execution import CanonicalGenerationExecutor
from .generate import _execution_identity, _interrupt_check
from .presentation import NODE_CATEGORIES, NODE_SEARCH_ALIASES, apply_input_presentation


class LlamaCppTranscribe:
    DESCRIPTION = (
        "Experimental short-clip transcription with the verified Qwen3-ASR pair. "
        "Requires one mono 16000 Hz AUDIO item, at most 10 seconds, and a managed "
        "direct server started with the approved model and audio projector."
    )
    CATEGORY = NODE_CATEGORIES["LlamaCppTranscribe"]
    SEARCH_ALIASES = NODE_SEARCH_ALIASES["LlamaCppTranscribe"]
    RETURN_TYPES = ("STRING", "STRING", "STRING", "LLAMACPP_GENERATION_RESULT", "STRING")
    RETURN_NAMES = ("transcript", "raw", "language", "result", "metadata_json")
    OUTPUT_TOOLTIPS = (
        "Transcript with only the verified leading English ASR prefix removed.",
        "The complete unmodified model response.",
        "English for the verified prefix; empty when the prefix is unrecognized or absent.",
        "Canonical completion, model, operation, timing, usage and release evidence.",
        "Audio provenance and prefix parsing status, without waveform data or local paths.",
    )
    FUNCTION = "transcribe"
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        schema = {
            "required": {
                "audio": ("AUDIO", {"tooltip": "One mono 16000 Hz PCM clip, at most 10 seconds."}),
                "supported_pair": (
                    [SUPPORTED_ASR_PAIR],
                    {"tooltip": "Select this exact model/projector pair in Start Server first."},
                ),
                "release_after_generation": (
                    "BOOLEAN",
                    {"default": False, "tooltip": "Wait for terminal owned runtime release."},
                ),
                "request_timeout": (
                    "INT",
                    {
                        "default": 60,
                        "min": 1,
                        "max": 3600,
                        "tooltip": "Total request budget in seconds.",
                    },
                ),
            },
            "optional": {
                "server_url": (
                    "STRING",
                    {
                        "default": "",
                        "forceInput": True,
                        "tooltip": "Start Server URL; empty uses the owned current runtime.",
                    },
                ),
            },
            "hidden": {
                "unique_id": "UNIQUE_ID",
                "dynprompt": "DYNPROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO",
            },
        }
        return apply_input_presentation("LlamaCppTranscribe", schema)

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        del kwargs
        return float("nan")

    def transcribe(
        self,
        audio: Any,
        supported_pair: str = SUPPORTED_ASR_PAIR,
        release_after_generation: bool = False,
        request_timeout: int = 60,
        server_url: str = "",
        unique_id: object = None,
        dynprompt: Any = None,
        extra_pnginfo: Any = None,
    ) -> dict[str, Any]:
        outputs = CanonicalGenerationExecutor().transcribe(
            audio,
            supported_pair=supported_pair,
            server_url=server_url,
            request_timeout=request_timeout,
            release_after_generation=release_after_generation,
            identity=_execution_identity(unique_id, dynprompt, extra_pnginfo),
            cancel_check=_interrupt_check,
        )
        return {"ui": {"text": (outputs[0],)}, "result": outputs}


NODE_CLASS_MAPPINGS = {"LlamaCppTranscribe": LlamaCppTranscribe}
NODE_DISPLAY_NAME_MAPPINGS = {"LlamaCppTranscribe": "llama.cpp Transcribe (Experimental)"}
