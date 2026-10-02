"""Independent per-image captions through one canonical batch coordinator."""

from __future__ import annotations

import operator
from collections.abc import Callable
from copy import deepcopy
from typing import Any

from ..generation.captions import MAX_CAPTION_IMAGE_BYTES, MAX_CAPTION_ITEMS, CaptionBatchSpec
from ..generation.execution import RUNNING_MODEL, CanonicalGenerationExecutor
from ..generation.images import image_tensor_to_data_urls_bounded
from .generate import LlamaCppGenerate, _execution_identity, _interrupt_check
from .presentation import CATEGORY_GENERATE

_GENERATE_OPTIONS = (
    "connection",
    "profile",
    "server_url",
    "model",
    "system_prompt",
    "thinking_mode",
    "max_tokens",
    "sampling_mode",
    "temperature",
    "top_p",
    "top_k",
    "min_p",
    "repeat_penalty",
    "presence_penalty",
    "frequency_penalty",
    "seed",
    "cache_prompt",
    "stop_sequences",
    "api_key_env",
    "verify_tls",
    "request_timeout",
    "release_after_generation",
    "partial_output_policy",
    "structured_output",
    "token_ban",
)


def _image_shape(images: Any) -> tuple[int, ...]:
    """Read shape before touching contents; a Comfy IMAGE is an indexable tensor."""
    try:
        shape = tuple(operator.index(value) for value in images.shape)
    except (AttributeError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError("caption images must expose a Comfy IMAGE shape") from exc
    if len(shape) not in (3, 4) or any(value <= 0 for value in shape):
        raise ValueError("caption images must have nonempty [H,W,C] or [B,H,W,C] shape")
    if shape[-1] not in (1, 3, 4):
        raise ValueError("caption images must have 1, 3, or 4 channels")
    if len(shape) == 4 and shape[0] > MAX_CAPTION_ITEMS:
        raise ValueError(f"caption batch must contain 1 to {MAX_CAPTION_ITEMS} items")
    return shape


def _prepare_images(
    images: Any, shape: tuple[int, ...], check: Callable[[], object] | None = None
) -> list[str]:
    def check_control() -> None:
        _interrupt_check()
        if check is not None:
            check()

    encoded: list[str] = []
    total_bytes = 0
    for index in range(shape[0] if len(shape) == 4 else 1):
        check_control()
        # Do not fall back to copying the whole batch for non-indexable wrappers.
        # Only this one frame may reach detach/cpu/numpy and PNG preparation.
        frame = images[index : index + 1] if len(shape) == 4 else images
        urls = image_tensor_to_data_urls_bounded(frame, interrupt_check=check_control)
        if len(urls) != 1:
            raise ValueError("each caption item must encode exactly one image")
        total_bytes += len(urls[0].encode("utf-8"))
        if total_bytes > MAX_CAPTION_IMAGE_BYTES:
            raise ValueError(f"caption images exceed {MAX_CAPTION_IMAGE_BYTES} encoded UTF-8 bytes")
        encoded.append(urls[0])
    check_control()
    return encoded


class LlamaCppCaptions:
    DESCRIPTION = (
        "Caption each image independently in order, with explicit IDs and one shared "
        "request deadline and final release. Inspect result_json before using marked partials."
    )
    CATEGORY = CATEGORY_GENERATE
    SEARCH_ALIASES = ["caption batch", "image captions", "dataset", "per image"]
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("captions", "result_json")
    OUTPUT_IS_LIST = (True, False)
    OUTPUT_TOOLTIPS = (
        "One caption per image, in order. Marked partial mode leaves noncomplete items blank.",
        "Aligned item IDs, states, generation evidence, and the group's release result.",
    )
    FUNCTION = "caption"
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        base = deepcopy(LlamaCppGenerate.INPUT_TYPES())
        optional = {name: base["optional"][name] for name in _GENERATE_OPTIONS}
        optional["request_timeout"][1]["tooltip"] = (
            "One overall deadline for preparation, all caption requests, and terminal release."
        )
        optional["partial_output_policy"][1]["tooltip"] = (
            "Strict default fails the group on any failure. Marked partials preserve every ID; "
            "check result_json before using blank noncomplete captions."
        )
        for name, label, tooltip in (
            (
                "ids_json",
                "Item IDs JSON",
                "Optional exact-length JSON list of unique text IDs. Empty uses zero-based indices.",
            ),
            (
                "prompts_json",
                "Per-item Prompts JSON",
                "Optional JSON string to broadcast or an exact-length list of prompts. Empty uses Prompt.",
            ),
            (
                "seeds_json",
                "Per-item Seeds JSON",
                "Optional JSON integer to broadcast or an exact-length list of seeds. Empty uses Seed.",
            ),
        ):
            optional[name] = (
                "STRING",
                {"default": "", "multiline": True, "display_name": label, "tooltip": tooltip},
            )
        return {
            "required": {
                "images": (
                    "IMAGE",
                    {
                        "display_name": "Images",
                        "tooltip": "A batch of 1 to 32 images, one independent request per image.",
                    },
                ),
                "prompt": base["required"]["prompt"],
            },
            "optional": optional,
            "hidden": base["hidden"],
        }

    @classmethod
    def VALIDATE_INPUTS(cls, model=RUNNING_MODEL):
        return LlamaCppGenerate.VALIDATE_INPUTS(model=model)

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        del kwargs
        return float("nan")

    def caption(
        self,
        images: Any,
        prompt: str,
        ids_json: str = "",
        prompts_json: str = "",
        seeds_json: str = "",
        seed: int = 0,
        unique_id: object = None,
        dynprompt: Any = None,
        extra_pnginfo: Any = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        shape = _image_shape(images)
        item_count = shape[0] if len(shape) == 4 else 1
        # Fail malformed alignment before any CPU transfer or runtime request.
        CaptionBatchSpec.from_inputs(
            item_count,
            prompt,
            seed,
            ids_json=ids_json,
            prompts_json=prompts_json,
            seeds_json=seeds_json,
        )

        def prepare_images(check: Callable[[], object] | None = None) -> list[str]:
            return _prepare_images(images, shape, check)

        progress = None
        try:
            from comfy.utils import ProgressBar

            bar = ProgressBar(item_count)

            def progress(completed: int, total: int) -> None:
                bar.update_absolute(completed, total)

        except Exception:
            # Progress is additive. Missing Comfy utilities or a frontend hook
            # must not prevent the canonical coordinator from owning the job.
            pass

        outputs = CanonicalGenerationExecutor().captions(
            prompt,
            prepare_images=prepare_images,
            item_count=item_count,
            ids_json=ids_json,
            prompts_json=prompts_json,
            seeds_json=seeds_json,
            seed=seed,
            identity=_execution_identity(unique_id, dynprompt, extra_pnginfo),
            cancel_check=_interrupt_check,
            progress=progress,
            **kwargs,
        )
        return {"ui": {"text": (outputs[1],)}, "result": outputs}


NODE_CLASS_MAPPINGS = {"LlamaCppCaptions": LlamaCppCaptions}
NODE_DISPLAY_NAME_MAPPINGS = {"LlamaCppCaptions": "llama.cpp Caption Batch"}

__all__ = ["LlamaCppCaptions", "NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
