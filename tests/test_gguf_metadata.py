from __future__ import annotations

import os
import struct
from pathlib import Path

import pytest

from models import gguf_metadata
from models.gguf_metadata import (
    GGUFChangedError,
    GGUFLimitError,
    InvalidGGUFError,
    clear_gguf_metadata_cache,
    read_gguf_metadata,
)

_UINT32 = 4
_INT32 = 5
_BOOL = 7
_STRING = 8
_ARRAY = 9
_UINT64 = 10


def _string(value: str, endian: str) -> bytes:
    encoded = value.encode()
    return struct.pack(endian + "Q", len(encoded)) + encoded


def _value(value: object, endian: str) -> tuple[int, bytes]:
    if isinstance(value, bool):
        return _BOOL, struct.pack(endian + "B", int(value))
    if isinstance(value, int):
        return _UINT32, struct.pack(endian + "I", value)
    if isinstance(value, str):
        return _STRING, _string(value, endian)
    if isinstance(value, (list, tuple)):
        if not value:
            element_type = _STRING
            payload = b""
        elif all(isinstance(item, bool) for item in value):
            element_type = _BOOL
            payload = b"".join(struct.pack(endian + "B", int(item)) for item in value)
        elif all(isinstance(item, str) for item in value):
            element_type = _STRING
            payload = b"".join(_string(item, endian) for item in value)
        else:
            raise TypeError(value)
        return (
            _ARRAY,
            struct.pack(endian + "IQ", element_type, len(value)) + payload,
        )
    raise TypeError(value)


def write_gguf(
    path: Path,
    metadata: dict[str, object] | None = None,
    tensors: list[tuple[str, tuple[int, ...], int, int]] | None = None,
    *,
    version: int = 3,
    endian: str = "<",
    include_payload: bool = True,
    preserve_offsets: bool = False,
) -> Path:
    metadata = metadata or {}
    tensors = tensors or []
    alignment_value = metadata.get("general.alignment", gguf_metadata.DEFAULT_ALIGNMENT)
    alignment = (
        alignment_value
        if isinstance(alignment_value, int)
        and not isinstance(alignment_value, bool)
        and alignment_value > 0
        and alignment_value & (alignment_value - 1) == 0
        else gguf_metadata.DEFAULT_ALIGNMENT
    )
    descriptors: list[tuple[str, tuple[int, ...], int, int, int]] = []
    expected_offset = 0
    for name, dimensions, tensor_type, requested_offset in tensors:
        layout = gguf_metadata._GGML_TYPE_LAYOUTS.get(tensor_type)
        tensor_size = 0
        if layout is not None and dimensions and dimensions[0] % layout[0] == 0:
            elements = 1
            for dimension in dimensions:
                elements *= dimension
            tensor_size = elements // layout[0] * layout[1]
        offset = requested_offset if preserve_offsets else expected_offset
        descriptors.append((name, dimensions, tensor_type, offset, tensor_size))
        expected_offset = (offset + tensor_size + alignment - 1) & -alignment

    body = bytearray(b"GGUF")
    body.extend(struct.pack(endian + "IQQ", version, len(descriptors), len(metadata)))
    for key, value in metadata.items():
        value_type, payload = _value(value, endian)
        if (
            key == "clip.minicpmv_version"
            and isinstance(value, int)
            and not isinstance(value, bool)
        ):
            value_type = _INT32
            payload = struct.pack(endian + "i", value)
        body.extend(_string(key, endian))
        body.extend(struct.pack(endian + "I", value_type))
        body.extend(payload)
    for name, dimensions, tensor_type, offset, _tensor_size in descriptors:
        body.extend(_string(name, endian))
        body.extend(struct.pack(endian + "I", len(dimensions)))
        body.extend(struct.pack(endian + ("Q" * len(dimensions)), *dimensions))
        body.extend(struct.pack(endian + "IQ", tensor_type, offset))
    data_offset = (len(body) + alignment - 1) & -alignment
    body.extend(b"\x00" * (data_offset - len(body)))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(body)
        if include_payload and descriptors:
            last_offset = max(offset + size for _, _, _, offset, size in descriptors)
            handle.truncate(data_offset + last_offset)
    return path


@pytest.fixture(autouse=True)
def _empty_metadata_cache():
    clear_gguf_metadata_cache()
    yield
    clear_gguf_metadata_cache()


@pytest.mark.parametrize("version", [2, 3])
def test_reads_selected_v2_and_v3_metadata_without_tensor_payload(tmp_path, version):
    path = write_gguf(
        tmp_path / f"model-v{version}.gguf",
        {
            "general.architecture": "qwen3vl",
            "general.type": "model",
            "general.name": "Qwen3 VL 4B Instruct",
            "general.tags": ["image-text-to-text", "test"],
            "qwen3vl.embedding_length": 2560,
            "qwen3vl.n_deepstack_layers": 3,
            "tokenizer.ggml.tokens": ["ignored", "metadata"],
        },
        [
            ("token_embd.weight", (2560, 32), 7, 0),
            ("mm.2.bias", (2560,), 1, 0),
            ("mm.input_projection.weight", (2560, 1), 1, 0),
            ("v.deepstack.5.fc2.weight", (4096, 2560), 1, 0),
            ("resampler.proj.weight", (2560, 2560), 1, 0),
        ],
        version=version,
    )

    result = read_gguf_metadata(path)

    assert result.version == version
    assert result.metadata_count == 7
    assert result.tensor_count == 5
    assert result.string("general.architecture") == "qwen3vl"
    assert result.integer("qwen3vl.embedding_length") == 2560
    assert result.strings("general.tags") == ("image-text-to-text", "test")
    assert "tokenizer.ggml.tokens" not in result.values
    assert [(tensor.name, tensor.dimensions) for tensor in result.tensors] == [
        ("mm.2.bias", (2560,)),
        ("mm.input_projection.weight", (2560, 1)),
        ("v.deepstack.5.fc2.weight", (4096, 2560)),
        ("resampler.proj.weight", (2560, 2560)),
    ]


def test_rejects_byte_swapped_big_endian_v3_metadata(tmp_path):
    path = write_gguf(
        tmp_path / "big-endian.gguf",
        {
            "general.architecture": "gemma4",
            "gemma4.embedding_length": 2816,
        },
        endian=">",
    )

    with pytest.raises(InvalidGGUFError, match="endianness"):
        read_gguf_metadata(path)


def test_selected_values_are_typed_and_immutable(tmp_path):
    path = write_gguf(
        tmp_path / "projector.gguf",
        {
            "clip.has_vision_encoder": True,
            "clip.has_minicpmv_projector": True,
            "clip.minicpmv_version": 6,
            "clip.vision.is_deepstack_layers": [False, True, False],
            "general.tags": ["image-text-to-text"],
        },
    )

    result = read_gguf_metadata(path)

    assert result.boolean("clip.has_vision_encoder") is True
    assert result.boolean("clip.has_minicpmv_projector") is True
    assert result.integer("clip.minicpmv_version") == 6
    assert result.booleans("clip.vision.is_deepstack_layers") == (False, True, False)
    assert result.integer("clip.has_vision_encoder") is None
    with pytest.raises(TypeError):
        result.values["general.name"] = "mutated"  # type: ignore[index]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"NOPE" + struct.pack("<IQQ", 3, 0, 0), "magic"),
        (b"GGUF" + struct.pack("<IQQ", 1, 0, 0), "version"),
        (b"GGUF" + struct.pack("<IQQ", 4, 0, 0), "version"),
        (b"GGUF" + struct.pack("<IQQ", 3, 0, 1), "truncated"),
    ],
)
def test_rejects_invalid_magic_version_and_truncation(tmp_path, payload, message):
    path = tmp_path / "invalid.gguf"
    path.write_bytes(payload)

    with pytest.raises(InvalidGGUFError, match=message):
        read_gguf_metadata(path)


def test_rejects_duplicate_metadata_keys(tmp_path):
    body = bytearray(b"GGUF")
    body.extend(struct.pack("<IQQ", 3, 0, 2))
    for value in ("first", "second"):
        body.extend(_string("general.name", "<"))
        body.extend(struct.pack("<I", _STRING))
        body.extend(_string(value, "<"))
    path = tmp_path / "duplicate.gguf"
    path.write_bytes(body)

    with pytest.raises(InvalidGGUFError, match="duplicated"):
        read_gguf_metadata(path)


def test_rejects_hostile_counts_lengths_arrays_and_tensor_shapes(tmp_path):
    excessive_metadata = tmp_path / "metadata-count.gguf"
    excessive_metadata.write_bytes(
        b"GGUF" + struct.pack("<IQQ", 3, 0, gguf_metadata.MAX_METADATA_ENTRIES + 1)
    )
    with pytest.raises(GGUFLimitError, match="metadata count"):
        read_gguf_metadata(excessive_metadata)

    excessive_tensors = tmp_path / "tensor-count.gguf"
    excessive_tensors.write_bytes(
        b"GGUF" + struct.pack("<IQQ", 3, gguf_metadata.MAX_TENSOR_DESCRIPTORS + 1, 0)
    )
    with pytest.raises(GGUFLimitError, match="tensor count"):
        read_gguf_metadata(excessive_tensors)

    long_key = tmp_path / "long-key.gguf"
    long_key.write_bytes(b"GGUF" + struct.pack("<IQQQ", 3, 0, 1, gguf_metadata.MAX_KEY_BYTES + 1))
    with pytest.raises(GGUFLimitError, match="string"):
        read_gguf_metadata(long_key)

    large_array = tmp_path / "large-array.gguf"
    body = bytearray(b"GGUF")
    body.extend(struct.pack("<IQQ", 3, 0, 1))
    body.extend(_string("general.tags", "<"))
    body.extend(struct.pack("<IIQ", _ARRAY, _STRING, gguf_metadata.MAX_ARRAY_ITEMS + 1))
    large_array.write_bytes(body)
    with pytest.raises(GGUFLimitError, match="array"):
        read_gguf_metadata(large_array)

    excessive_dims = tmp_path / "dims.gguf"
    body = bytearray(b"GGUF")
    body.extend(struct.pack("<IQQ", 3, 1, 0))
    body.extend(_string("tensor", "<"))
    body.extend(struct.pack("<I", gguf_metadata.MAX_TENSOR_DIMS + 1))
    excessive_dims.write_bytes(body)
    with pytest.raises(GGUFLimitError, match="dimension count"):
        read_gguf_metadata(excessive_dims)


def test_rejects_unknown_value_types_nested_arrays_and_invalid_booleans(tmp_path):
    unknown = tmp_path / "unknown-type.gguf"
    body = bytearray(b"GGUF")
    body.extend(struct.pack("<IQQ", 3, 0, 1))
    body.extend(_string("general.name", "<"))
    body.extend(struct.pack("<I", 99))
    unknown.write_bytes(body)
    with pytest.raises(InvalidGGUFError, match="unknown value type"):
        read_gguf_metadata(unknown)

    nested = tmp_path / "nested-array.gguf"
    body = bytearray(b"GGUF")
    body.extend(struct.pack("<IQQ", 3, 0, 1))
    body.extend(_string("general.tags", "<"))
    body.extend(struct.pack("<IIQ", _ARRAY, _ARRAY, 0))
    nested.write_bytes(body)
    with pytest.raises(InvalidGGUFError, match="Nested"):
        read_gguf_metadata(nested)

    invalid_bool = tmp_path / "invalid-bool.gguf"
    body = bytearray(b"GGUF")
    body.extend(struct.pack("<IQQ", 3, 0, 1))
    body.extend(_string("clip.has_vision_encoder", "<"))
    body.extend(struct.pack("<IB", _BOOL, 2))
    invalid_bool.write_bytes(body)
    with pytest.raises(InvalidGGUFError, match="boolean"):
        read_gguf_metadata(invalid_bool)


@pytest.mark.parametrize(
    ("key", "wrong_type", "payload"),
    [
        ("general.alignment", _UINT64, struct.pack("<Q", 32)),
        ("general.file_type", _INT32, struct.pack("<i", 7)),
        ("general.base_model.count", _UINT64, struct.pack("<Q", 1)),
        ("clip.vision.projection_dim", _UINT64, struct.pack("<Q", 2560)),
        ("clip.vision.embedding_length", _UINT64, struct.pack("<Q", 1024)),
        ("qwen3vl.embedding_length", _UINT64, struct.pack("<Q", 2560)),
        ("qwen3vl.n_deepstack_layers", _UINT64, struct.pack("<Q", 3)),
        ("clip.minicpmv_version", _UINT32, struct.pack("<I", 6)),
    ],
)
def test_rejects_compatibility_integers_with_wrong_gguf_scalar_type(
    tmp_path,
    key,
    wrong_type,
    payload,
):
    body = bytearray(b"GGUF")
    body.extend(struct.pack("<IQQ", 3, 0, 1))
    body.extend(_string(key, "<"))
    body.extend(struct.pack("<I", wrong_type))
    body.extend(payload)
    body.extend(b"\x00" * ((-len(body)) % gguf_metadata.DEFAULT_ALIGNMENT))
    path = tmp_path / f"{key.replace('.', '-')}.gguf"
    path.write_bytes(body)

    with pytest.raises(InvalidGGUFError, match="must use scalar type"):
        read_gguf_metadata(path)


def test_validates_tensor_types_blocks_names_offsets_alignment_and_payload(tmp_path):
    duplicate = write_gguf(
        tmp_path / "duplicate-tensor.gguf",
        tensors=[
            ("same", (32,), 2, 0),
            ("same", (32,), 2, 0),
        ],
    )
    with pytest.raises(InvalidGGUFError, match="tensor name is duplicated"):
        read_gguf_metadata(duplicate)

    removed_type = write_gguf(
        tmp_path / "removed-type.gguf",
        tensors=[("tensor", (32,), 4, 0)],
    )
    with pytest.raises(InvalidGGUFError, match="unsupported type"):
        read_gguf_metadata(removed_type)

    invalid_block = write_gguf(
        tmp_path / "invalid-block.gguf",
        tensors=[("tensor", (31,), 2, 0)],
    )
    with pytest.raises(InvalidGGUFError, match="not divisible"):
        read_gguf_metadata(invalid_block)

    invalid_offset = write_gguf(
        tmp_path / "invalid-offset.gguf",
        tensors=[
            ("first", (32,), 2, 0),
            ("second", (32,), 2, 1),
        ],
        preserve_offsets=True,
    )
    with pytest.raises(InvalidGGUFError, match="not sequential"):
        read_gguf_metadata(invalid_offset)

    invalid_alignment = write_gguf(
        tmp_path / "invalid-alignment.gguf",
        {"general.alignment": 3},
    )
    with pytest.raises(InvalidGGUFError, match="power of two"):
        read_gguf_metadata(invalid_alignment)

    missing_payload = write_gguf(
        tmp_path / "missing-payload.gguf",
        tensors=[("tensor", (32,), 2, 0)],
        include_payload=False,
    )
    with pytest.raises(InvalidGGUFError, match="payload is truncated"):
        read_gguf_metadata(missing_payload)


def test_retained_state_has_per_file_and_weighted_cache_budgets(tmp_path, monkeypatch):
    oversized_metadata = {f"ignored.{index:04d}." + ("x" * 880): index for index in range(1_200)}
    oversized = write_gguf(tmp_path / "retained-limit.gguf", oversized_metadata)
    with pytest.raises(GGUFLimitError, match="retained metadata"):
        read_gguf_metadata(oversized)

    monkeypatch.setattr(gguf_metadata, "METADATA_CACHE_BYTE_BUDGET", 1_200)
    for index in range(6):
        read_gguf_metadata(
            write_gguf(
                tmp_path / f"weighted-{index}.gguf",
                {"general.name": f"Weighted model {index}"},
            )
        )

    assert gguf_metadata._CACHE_BYTES <= gguf_metadata.METADATA_CACHE_BYTE_BUDGET
    assert len(gguf_metadata._CACHE) < 6


def test_call_specific_scan_limit_applies_to_cached_results(tmp_path):
    path = write_gguf(
        tmp_path / "scan-limit.gguf",
        {"general.name": "Cached header"},
    )
    result = read_gguf_metadata(path)
    assert result.scanned_bytes > 1

    with pytest.raises(GGUFLimitError, match="inspection limit"):
        read_gguf_metadata(path, scan_limit=result.scanned_bytes - 1)


def test_cache_reuses_stat_stable_result_and_invalidates_replacement(tmp_path):
    path = write_gguf(
        tmp_path / "cached.gguf",
        {"general.name": "Before"},
    )

    first = read_gguf_metadata(path)
    second = read_gguf_metadata(path)
    assert second is first

    write_gguf(
        path,
        {"general.name": "After replacement with a different file size"},
    )
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000))
    replacement = read_gguf_metadata(path)

    assert replacement is not first
    assert replacement.string("general.name") == "After replacement with a different file size"


def test_path_and_handle_ctime_may_differ_without_claiming_file_mutation(
    tmp_path,
    monkeypatch,
):
    path = write_gguf(
        tmp_path / "windows-ctime.gguf",
        {"general.name": "Stable across Windows stat surfaces"},
    )
    real_fstat = gguf_metadata.os.fstat

    class HandleStat:
        def __init__(self, source):
            self.st_dev = source.st_dev
            self.st_ino = source.st_ino
            self.st_size = source.st_size
            self.st_mtime_ns = source.st_mtime_ns
            self.st_ctime_ns = source.st_ctime_ns + 1_000_000_000

    monkeypatch.setattr(
        gguf_metadata.os,
        "fstat",
        lambda descriptor: HandleStat(real_fstat(descriptor)),
    )

    result = read_gguf_metadata(path)

    assert result.string("general.name") == "Stable across Windows stat surfaces"


def test_cache_is_bounded_lru(tmp_path):
    for index in range(gguf_metadata.METADATA_CACHE_SIZE + 5):
        path = write_gguf(
            tmp_path / f"{index}.gguf",
            {"general.name": f"Model {index}"},
        )
        read_gguf_metadata(path)

    assert len(gguf_metadata._CACHE) == gguf_metadata.METADATA_CACHE_SIZE


def test_detects_file_mutation_during_parse(tmp_path, monkeypatch):
    path = write_gguf(
        tmp_path / "changing.gguf",
        {"general.name": "Before"},
    )
    original_parse = gguf_metadata._parse_file

    def parse_then_change(handle, resolved, *, file_size, scan_limit):
        result = original_parse(
            handle,
            resolved,
            file_size=file_size,
            scan_limit=scan_limit,
        )
        with resolved.open("ab") as output:
            output.write(b"x")
        return result

    monkeypatch.setattr(gguf_metadata, "_parse_file", parse_then_change)

    with pytest.raises(GGUFChangedError, match="changed"):
        read_gguf_metadata(path)
