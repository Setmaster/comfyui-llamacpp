"""Bounded, explicit inputs and outputs for independent per-image caption jobs."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .contracts import MAX_REQUEST_TEXT_CHARS, GenerationResult

MAX_CAPTION_ITEMS = 32
MAX_CAPTION_IMAGE_BYTES = 32 * 1024 * 1024
MAX_CAPTION_RESULT_BYTES = 8 * 1024 * 1024
MAX_CAPTION_INPUT_JSON_BYTES = 1024 * 1024
MAX_CAPTION_ID_CHARS = 128
MAX_CAPTION_SEED = 0x7FFFFFFF
_UNSET = object()


def _text(value: Any, name: str, maximum: int, *, nonempty: bool = False) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be text")
    if len(value) > maximum or (nonempty and not value.strip()):
        raise ValueError(
            f"{name} must contain {'1 to ' if nonempty else 'at most '}{maximum} characters"
        )
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{name} must be valid UTF-8 text") from exc
    return value


def _seed(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= MAX_CAPTION_SEED:
        raise ValueError(f"caption seed must be an integer between 0 and {MAX_CAPTION_SEED}")
    return value


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("caption input JSON contains a duplicate key")
        value[key] = item
    return value


def _constant(value: str) -> None:
    raise ValueError("caption input JSON must not contain nonfinite numbers")


def _input_json(text: str, name: str) -> Any:
    if not isinstance(text, str):
        raise TypeError(f"{name} must be JSON text")
    try:
        encoded = text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{name} must be valid UTF-8 JSON") from exc
    if len(encoded) > MAX_CAPTION_INPUT_JSON_BYTES:
        raise ValueError(f"{name} exceeds {MAX_CAPTION_INPUT_JSON_BYTES} UTF-8 bytes")
    if not text.strip():
        return _UNSET
    try:
        return json.loads(text, object_pairs_hook=_object, parse_constant=_constant)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"{name} must be valid bounded JSON") from exc


def _paired(value: Any, fallback: Any, item_count: int, name: str) -> list[Any]:
    if value is _UNSET:
        return [fallback] * item_count
    if isinstance(value, list):
        if len(value) != item_count:
            raise ValueError(f"{name} must contain exactly {item_count} entries")
        return value
    return [value] * item_count


@dataclass(frozen=True, slots=True)
class CaptionItemSpec:
    item_id: str
    prompt: str
    seed: int

    def __post_init__(self) -> None:
        _text(self.item_id, "caption item ID", MAX_CAPTION_ID_CHARS, nonempty=True)
        _text(self.prompt, "caption prompt", MAX_REQUEST_TEXT_CHARS)
        _seed(self.seed)


@dataclass(frozen=True, slots=True)
class CaptionBatchSpec:
    rows: tuple[CaptionItemSpec, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.rows, (tuple, list)):
            raise TypeError("caption rows must be an ordered sequence")
        if not 1 <= len(self.rows) <= MAX_CAPTION_ITEMS:
            raise ValueError(f"caption batch must contain 1 to {MAX_CAPTION_ITEMS} items")
        if any(not isinstance(row, CaptionItemSpec) for row in self.rows):
            raise TypeError("caption rows must contain CaptionItemSpec values")
        if len({row.item_id for row in self.rows}) != len(self.rows):
            raise ValueError("caption item IDs must be unique")
        object.__setattr__(self, "rows", tuple(self.rows))

    @property
    def item_count(self) -> int:
        return len(self.rows)

    @classmethod
    def from_inputs(
        cls,
        item_count: int,
        prompt: str,
        seed: int = 0,
        *,
        ids_json: str = "",
        prompts_json: str = "",
        seeds_json: str = "",
    ) -> CaptionBatchSpec:
        if type(item_count) is not int or not 1 <= item_count <= MAX_CAPTION_ITEMS:
            raise ValueError(f"caption batch must contain 1 to {MAX_CAPTION_ITEMS} items")
        _text(prompt, "caption prompt", MAX_REQUEST_TEXT_CHARS)
        _seed(seed)
        ids = _input_json(ids_json, "IDs JSON")
        if ids is _UNSET:
            ids = [str(index) for index in range(item_count)]
        if not isinstance(ids, list) or len(ids) != item_count:
            raise ValueError(f"IDs JSON must be a list of exactly {item_count} IDs")
        prompts = _paired(
            _input_json(prompts_json, "Prompts JSON"), prompt, item_count, "Prompts JSON"
        )
        seeds = _paired(_input_json(seeds_json, "Seeds JSON"), seed, item_count, "Seeds JSON")
        return cls(
            tuple(
                CaptionItemSpec(item_id, item_prompt, item_seed)
                for item_id, item_prompt, item_seed in zip(ids, prompts, seeds, strict=True)
            )
        )


def _bounded_result_json(value: Mapping[str, Any]) -> str:
    encoder = json.JSONEncoder(
        ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
    )
    parts: list[str] = []
    size = 0
    try:
        for part in encoder.iterencode(value):
            size += len(part.encode("utf-8"))
            if size > MAX_CAPTION_RESULT_BYTES:
                raise ValueError(
                    f"caption result exceeds {MAX_CAPTION_RESULT_BYTES} serialized UTF-8 bytes"
                )
            parts.append(part)
    except (UnicodeEncodeError, RecursionError) as exc:
        raise ValueError("caption result must be bounded valid UTF-8 JSON") from exc
    return "".join(parts)


def caption_batch_outputs(
    spec: CaptionBatchSpec,
    rows: Sequence[Mapping[str, Any]],
    *,
    state: str,
    release: Mapping[str, Any],
    warnings: Sequence[str] = (),
) -> tuple[list[str], str]:
    """Serialize aligned evidence; the coordinator owns the partial-output policy.

    A failed or cancelled group may contain completed rows, but only those rows
    produce a caption. Their positions remain stable even when later work stops.
    """
    if not isinstance(spec, CaptionBatchSpec):
        raise TypeError("caption outputs require CaptionBatchSpec")
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
        raise TypeError("caption result rows must be an ordered sequence")
    if len(rows) != spec.item_count:
        raise ValueError("caption result rows must match the complete input count")
    if state not in ("complete", "partial", "failed", "cancelled"):
        raise ValueError("caption group state must be complete, partial, failed, or cancelled")
    if not isinstance(release, Mapping):
        raise TypeError("caption group release must be a mapping")
    if isinstance(warnings, (str, bytes)) or not isinstance(warnings, Sequence):
        raise TypeError("caption group warnings must be a sequence of strings")
    for warning in warnings:
        _text(warning, "caption warning", MAX_CAPTION_RESULT_BYTES)

    prepared: list[dict[str, Any]] = []
    expected = {"item_id", "seed", "state", "response", "thinking", "result", "error"}
    for item, row in zip(spec.rows, rows, strict=True):
        if not isinstance(row, Mapping) or set(row) != expected:
            raise ValueError("caption result row fields must match the caption row schema")
        if row["item_id"] != item.item_id or _seed(row["seed"]) != item.seed:
            raise ValueError("caption result IDs and seeds must match input order exactly")
        row_state = row["state"]
        if row_state not in ("complete", "failed", "not_attempted"):
            raise ValueError("caption row state must be complete, failed, or not_attempted")
        for field in ("response", "thinking"):
            _text(row[field], f"caption row {field}", MAX_CAPTION_RESULT_BYTES)
        result, error = row["result"], row["error"]
        if result is not None and not isinstance(result, Mapping):
            raise TypeError("caption row result must be a canonical result mapping or null")
        if error is not None:
            if not isinstance(error, Mapping) or not {"category", "message"} <= error.keys():
                raise TypeError("caption row error must contain category and message or be null")
            _text(error["category"], "caption error category", 64, nonempty=True)
            _text(
                error["message"], "caption error message", MAX_CAPTION_RESULT_BYTES, nonempty=True
            )
        if row_state == "complete" and (result is None or error is not None or not row["response"]):
            raise ValueError(
                "a complete caption row requires a response and result without an error"
            )
        if row_state == "failed" and error is None:
            raise ValueError("a failed caption row requires an error")
        if row_state == "not_attempted" and any(
            (row["response"], row["thinking"], result is not None, error is not None)
        ):
            raise ValueError(
                "an unattempted caption row cannot contain response or execution evidence"
            )
        prepared.append(
            {
                **row,
                "result": dict(result) if result is not None else None,
                "error": dict(error) if error is not None else None,
            }
        )

    if state == "complete" and any(row["state"] != "complete" for row in prepared):
        raise ValueError("a complete caption group requires every row to be complete")
    serialized = _bounded_result_json(
        {
            "schema_version": 1,
            "operation": "captions",
            "state": state,
            "rows": prepared,
            "release": dict(release),
            "warnings": list(warnings),
        }
    )
    # Bound the complete envelope before parsing canonical results, which can
    # otherwise each allow a much larger standalone generation result.
    for row in prepared:
        if row["result"] is None:
            continue
        result = GenerationResult.from_dict(row["result"])
        if (
            result.seed != row["seed"]
            or result.response != row["response"]
            or result.thinking != row["thinking"]
        ):
            raise ValueError("caption row text and seed must match its canonical result")
        if row["state"] == "complete" and (
            not result.success or result.image_count != 1 or not result.effective_model
        ):
            raise ValueError(
                "a complete caption requires one-image completion and exact model evidence"
            )
        if row["state"] == "failed":
            if result.success or result.error is None:
                raise ValueError(
                    "a failed caption row cannot contain a successful canonical result"
                )
            if (row["error"]["category"], row["error"]["message"]) != (
                result.error.category.value,
                result.error.message,
            ):
                raise ValueError("caption row error must match its canonical result")
    return [row["response"] if row["state"] == "complete" else "" for row in prepared], serialized
