"""Strict ComfyUI image conversion for llama-server multimodal requests."""

from __future__ import annotations

import base64
import io
from collections.abc import Callable
from typing import Any

import numpy as np
from PIL import Image


def _as_numpy(tensor: Any) -> np.ndarray:
    value = tensor
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "numpy"):
        value = value.numpy()
    array = np.asarray(value)
    if array.ndim not in (3, 4):
        raise ValueError(f"Expected IMAGE shape [H,W,C] or [B,H,W,C], got {array.shape}")
    return array


def _encode_frame(frame: np.ndarray) -> str:
    if frame.ndim != 3 or frame.shape[-1] not in (1, 3, 4):
        raise ValueError(f"Expected 1, 3, or 4 image channels, got {frame.shape}")
    if np.issubdtype(frame.dtype, np.floating):
        frame = np.nan_to_num(frame, nan=0.0, posinf=1.0, neginf=0.0)
        frame = np.rint(np.clip(frame, 0.0, 1.0) * 255.0).astype(np.uint8)
    else:
        frame = np.clip(frame, 0, 255).astype(np.uint8)
    if frame.shape[-1] == 1:
        frame = frame[..., 0]

    image = Image.fromarray(frame)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def image_tensor_to_data_urls(tensor: Any, *, include_batch: bool = False) -> list[str]:
    """Convert a Comfy IMAGE to PNG data URLs.

    The legacy behavior uses only the first image in a batch. Callers can opt
    into sending the complete batch without changing existing workflows.
    """

    array = _as_numpy(tensor)
    frames = array if array.ndim == 4 else array[np.newaxis, ...]
    if not include_batch:
        frames = frames[:1]
    return [_encode_frame(frame) for frame in frames]


def image_tensor_frame_count(tensor: Any, *, include_batch: bool = False) -> int:
    """Return the number of frames a conversion would encode without encoding them."""

    array = _as_numpy(tensor)
    if array.ndim == 3 or not include_batch:
        return 1
    return int(array.shape[0])


def image_tensor_to_data_urls_bounded(
    tensor: Any,
    *,
    include_batch: bool = False,
    interrupt_check: Callable[[], object] | None = None,
) -> list[str]:
    """Encode frames while allowing a caller to observe interruption between frames."""

    array = _as_numpy(tensor)
    frames = array if array.ndim == 4 else array[np.newaxis, ...]
    if not include_batch:
        frames = frames[:1]
    encoded: list[str] = []
    for frame in frames:
        if interrupt_check is not None:
            interrupt_check()
        encoded.append(_encode_frame(frame))
    return encoded


def image_tensor_to_data_url(tensor: Any) -> str:
    return image_tensor_to_data_urls(tensor, include_batch=False)[0]
