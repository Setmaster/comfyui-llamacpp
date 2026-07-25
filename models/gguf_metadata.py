"""Bounded, dependency-free GGUF metadata inspection.

The projector resolver needs a small set of identity and interface facts from
GGUF files.  Loading a model, importing NumPy, or retaining tokenizer metadata
would make that lookup unnecessarily expensive.  This module instead walks the
GGUF v2/v3 header, retains only allow-listed metadata and tensor descriptors,
and stops before tensor payloads.

GGUF files are untrusted input.  Every variable-size field and loop is bounded,
and a file must remain stat-stable for the duration of the read before its
immutable result enters the small LRU cache.
"""

from __future__ import annotations

import os
import re
import struct
import threading
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import BinaryIO, TypeAlias

GGUF_MAGIC = b"GGUF"
SUPPORTED_VERSIONS = frozenset((2, 3))

MAX_HEADER_BYTES = 64 * 1024 * 1024
MAX_METADATA_ENTRIES = 4_096
MAX_TENSOR_DESCRIPTORS = 65_536
MAX_KEY_BYTES = 1_024
MAX_STRING_BYTES = 16 * 1024 * 1024
MAX_SELECTED_STRING_BYTES = 64 * 1024
MAX_ARRAY_ITEMS = 1_000_000
MAX_SELECTED_ARRAY_ITEMS = 4_096
MAX_TENSOR_NAME_BYTES = 1_024
MAX_TENSOR_DIMS = 4
MAX_TENSOR_DIMENSION = 1 << 40
MAX_TENSOR_ELEMENTS = (1 << 63) - 1
MAX_SELECTED_TENSORS = 512
METADATA_CACHE_SIZE = 64
MAX_RETAINED_BYTES_PER_FILE = 1024 * 1024
METADATA_CACHE_BYTE_BUDGET = 8 * 1024 * 1024
DEFAULT_ALIGNMENT = 32
MAX_ALIGNMENT = 1024 * 1024

# llama.cpp b9957 GGML types. Removed/unused numeric slots are deliberately
# absent: accepting them would make tensor payload sizing impossible.
# Values are (elements per block, serialized bytes per block).
_GGML_TYPE_LAYOUTS: Mapping[int, tuple[int, int]] = MappingProxyType(
    {
        0: (1, 4),
        1: (1, 2),
        2: (32, 18),
        3: (32, 20),
        6: (32, 22),
        7: (32, 24),
        8: (32, 34),
        9: (32, 40),
        10: (256, 84),
        11: (256, 110),
        12: (256, 144),
        13: (256, 176),
        14: (256, 210),
        15: (256, 292),
        16: (256, 66),
        17: (256, 74),
        18: (256, 98),
        19: (256, 50),
        20: (32, 18),
        21: (256, 110),
        22: (256, 82),
        23: (256, 136),
        24: (1, 1),
        25: (1, 2),
        26: (1, 4),
        27: (1, 8),
        28: (1, 8),
        29: (256, 56),
        30: (1, 2),
        34: (256, 54),
        35: (256, 66),
        39: (32, 17),
        40: (64, 36),
        41: (128, 18),
        42: (64, 18),
    }
)


class GGUFMetadataError(ValueError):
    """Base class for a GGUF header that cannot be inspected safely."""

    scanned_bytes: int = 0


class InvalidGGUFError(GGUFMetadataError):
    """The file is not a supported, structurally valid GGUF v2/v3 file."""


class GGUFLimitError(GGUFMetadataError):
    """The file exceeds a defensive parser bound."""


class GGUFChangedError(GGUFMetadataError):
    """The file changed or was replaced while its header was inspected."""


GGUFScalar: TypeAlias = str | int | float | bool
GGUFValue: TypeAlias = GGUFScalar | tuple[GGUFScalar, ...]


@dataclass(frozen=True, slots=True)
class TensorDescriptor:
    """A selected GGUF tensor descriptor, without tensor data."""

    name: str
    dimensions: tuple[int, ...]
    tensor_type: int
    offset: int


@dataclass(frozen=True, slots=True)
class GGUFMetadata:
    """Immutable selected facts from one stat-stable GGUF header."""

    path: Path
    version: int
    metadata_count: int
    tensor_count: int
    values: Mapping[str, GGUFValue]
    tensors: tuple[TensorDescriptor, ...]
    alignment: int
    data_offset: int
    data_size: int
    scanned_bytes: int
    retained_bytes: int

    def string(self, key: str) -> str | None:
        value = self.values.get(key)
        return value if isinstance(value, str) else None

    def integer(self, key: str) -> int | None:
        value = self.values.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def boolean(self, key: str) -> bool | None:
        value = self.values.get(key)
        return value if isinstance(value, bool) else None

    def strings(self, key: str) -> tuple[str, ...] | None:
        value = self.values.get(key)
        if isinstance(value, tuple) and all(isinstance(item, str) for item in value):
            return value
        return None

    def booleans(self, key: str) -> tuple[bool, ...] | None:
        value = self.values.get(key)
        if isinstance(value, tuple) and all(isinstance(item, bool) for item in value):
            return value
        return None


_UINT8 = 0
_INT8 = 1
_UINT16 = 2
_INT16 = 3
_UINT32 = 4
_INT32 = 5
_FLOAT32 = 6
_BOOL = 7
_STRING = 8
_ARRAY = 9
_UINT64 = 10
_INT64 = 11
_FLOAT64 = 12

_SCALAR_FORMATS = {
    _UINT8: "B",
    _INT8: "b",
    _UINT16: "H",
    _INT16: "h",
    _UINT32: "I",
    _INT32: "i",
    _FLOAT32: "f",
    _BOOL: "B",
    _UINT64: "Q",
    _INT64: "q",
    _FLOAT64: "d",
}

_EXACT_METADATA_KEYS = frozenset(
    {
        "general.architecture",
        "general.type",
        "general.name",
        "general.basename",
        "general.finetune",
        "general.version",
        "general.size_label",
        "general.description",
        "general.tags",
        "general.file_type",
        "general.alignment",
        "general.base_model.count",
        "clip.has_vision_encoder",
        "clip.has_minicpmv_projector",
        "clip.minicpmv_version",
        "clip.projector_type",
        "clip.vision.projector_type",
        "clip.vision.projection_dim",
        "clip.vision.embedding_length",
        "clip.vision.is_deepstack_layers",
    }
)
_BASE_MODEL_KEY = re.compile(r"^general\.base_model\.\d+\.(?:name|repo_url)$")
_ARCHITECTURE_KEY = re.compile(r"^[A-Za-z0-9_.-]+\.(?:embedding_length|n_deepstack_layers)$")
_SELECTED_TENSOR = re.compile(r"^v\.deepstack\.\d+\.fc[12]\.weight$")
_UINT32_METADATA_KEYS = frozenset(
    {
        "general.alignment",
        "general.file_type",
        "general.base_model.count",
        "clip.vision.projection_dim",
        "clip.vision.embedding_length",
    }
)


def _selected_metadata_key(key: str) -> bool:
    return (
        key in _EXACT_METADATA_KEYS
        or _BASE_MODEL_KEY.fullmatch(key) is not None
        or _ARCHITECTURE_KEY.fullmatch(key) is not None
    )


def _selected_tensor_name(name: str) -> bool:
    return (
        name
        in {
            "mm.2.bias",
            "mm.input_projection.weight",
            "resampler.proj.weight",
        }
        or _SELECTED_TENSOR.fullmatch(name) is not None
    )


def _expected_integer_type(key: str) -> int | None:
    if key == "clip.minicpmv_version":
        return _INT32
    if key in _UINT32_METADATA_KEYS or _ARCHITECTURE_KEY.fullmatch(key) is not None:
        return _UINT32
    return None


class _RetainedBudget:
    """Account for text/value state retained while validating one header."""

    def __init__(self) -> None:
        self.used = 0

    def charge(self, size: int) -> None:
        if size < 0 or self.used > MAX_RETAINED_BYTES_PER_FILE - size:
            raise GGUFLimitError(
                "GGUF retained metadata exceeds the "
                f"{MAX_RETAINED_BYTES_PER_FILE}-byte per-file limit"
            )
        self.used += size


def _retained_text_size(value: str) -> int:
    return 64 + len(value.encode("utf-8"))


def _align(value: int, alignment: int) -> int:
    remainder = value & (alignment - 1)
    return value if remainder == 0 else value + alignment - remainder


def _tensor_nbytes(dimensions: tuple[int, ...], tensor_type: int) -> int:
    layout = _GGML_TYPE_LAYOUTS.get(tensor_type)
    if layout is None:
        raise InvalidGGUFError(f"GGUF tensor uses unsupported type {tensor_type}")
    block_size, type_size = layout
    if dimensions[0] % block_size:
        raise InvalidGGUFError(
            f"GGUF tensor first dimension is not divisible by type {tensor_type} block size"
        )
    elements = 1
    for dimension in dimensions:
        if elements > MAX_TENSOR_ELEMENTS // dimension:
            raise GGUFLimitError("GGUF tensor element count exceeds the inspection limit")
        elements *= dimension
    blocks = elements // block_size
    if blocks > MAX_TENSOR_ELEMENTS // type_size:
        raise GGUFLimitError("GGUF tensor byte size exceeds the inspection limit")
    return blocks * type_size


class _HeaderReader:
    def __init__(self, handle: BinaryIO, *, file_size: int, scan_limit: int):
        self._handle = handle
        self._file_size = file_size
        self._scan_limit = scan_limit
        self.endian = "<"

    @property
    def offset(self) -> int:
        return self._handle.tell()

    def _check_advance(self, size: int) -> None:
        if size < 0:
            raise InvalidGGUFError("GGUF field has a negative size")
        target = self.offset + size
        if target > self._file_size:
            raise InvalidGGUFError("GGUF header is truncated")
        if target > self._scan_limit:
            raise GGUFLimitError(
                f"GGUF header exceeds the {self._scan_limit}-byte inspection limit"
            )

    def read_exact(self, size: int) -> bytes:
        self._check_advance(size)
        value = self._handle.read(size)
        if len(value) != size:
            raise InvalidGGUFError("GGUF header is truncated")
        return value

    def skip(self, size: int) -> None:
        self._check_advance(size)
        self._handle.seek(size, os.SEEK_CUR)

    def unpack(self, fmt: str) -> int | float:
        size = struct.calcsize(fmt)
        return struct.unpack(self.endian + fmt, self.read_exact(size))[0]

    def read_string(
        self,
        *,
        keep: bool,
        length_limit: int = MAX_STRING_BYTES,
        retained_budget: _RetainedBudget | None = None,
    ) -> str | None:
        length = int(self.unpack("Q"))
        if length > length_limit:
            raise GGUFLimitError(f"GGUF string exceeds the {length_limit}-byte limit")
        if not keep:
            self.skip(length)
            return None
        raw = self.read_exact(length)
        try:
            value = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InvalidGGUFError("GGUF string is not valid UTF-8") from exc
        if retained_budget is not None:
            retained_budget.charge(_retained_text_size(value))
        return value

    def read_value(
        self,
        value_type: int,
        *,
        keep: bool,
        retained_budget: _RetainedBudget | None = None,
    ) -> GGUFValue | None:
        if value_type in _SCALAR_FORMATS:
            fmt = _SCALAR_FORMATS[value_type]
            if not keep:
                self.skip(struct.calcsize(fmt))
                return None
            if retained_budget is not None:
                retained_budget.charge(16)
            value = self.unpack(fmt)
            if value_type == _BOOL:
                if value not in (0, 1):
                    raise InvalidGGUFError("GGUF boolean value is not 0 or 1")
                return bool(value)
            return value

        if value_type == _STRING:
            return self.read_string(
                keep=keep,
                length_limit=MAX_SELECTED_STRING_BYTES if keep else MAX_STRING_BYTES,
                retained_budget=retained_budget if keep else None,
            )

        if value_type != _ARRAY:
            raise InvalidGGUFError(f"GGUF metadata uses unknown value type {value_type}")

        element_type = int(self.unpack("I"))
        if element_type == _ARRAY:
            raise InvalidGGUFError("Nested GGUF metadata arrays are not supported by GGUF v2/v3")
        count = int(self.unpack("Q"))
        if count > MAX_ARRAY_ITEMS:
            raise GGUFLimitError(f"GGUF array exceeds the {MAX_ARRAY_ITEMS}-item limit")
        if keep and count > MAX_SELECTED_ARRAY_ITEMS:
            raise GGUFLimitError(
                f"Selected GGUF array exceeds the {MAX_SELECTED_ARRAY_ITEMS}-item limit"
            )

        if element_type in _SCALAR_FORMATS:
            fmt = _SCALAR_FORMATS[element_type]
            size = struct.calcsize(fmt)
            if not keep:
                self.skip(size * count)
                return None
            if retained_budget is not None:
                retained_budget.charge(64 + 16 * count)
            values: list[GGUFScalar] = []
            for _ in range(count):
                value = self.unpack(fmt)
                if element_type == _BOOL:
                    if value not in (0, 1):
                        raise InvalidGGUFError("GGUF boolean value is not 0 or 1")
                    values.append(bool(value))
                else:
                    values.append(value)
            return tuple(values)

        if element_type == _STRING:
            if keep and retained_budget is not None:
                retained_budget.charge(64)
            strings: list[GGUFScalar] = []
            for _ in range(count):
                value = self.read_string(
                    keep=keep,
                    length_limit=MAX_SELECTED_STRING_BYTES if keep else MAX_STRING_BYTES,
                    retained_budget=retained_budget if keep else None,
                )
                if keep:
                    assert value is not None
                    strings.append(value)
            return tuple(strings) if keep else None

        raise InvalidGGUFError(f"GGUF array uses unknown element type {element_type}")


def _parse_file(
    handle: BinaryIO,
    path: Path,
    *,
    file_size: int,
    scan_limit: int,
) -> GGUFMetadata:
    reader = _HeaderReader(handle, file_size=file_size, scan_limit=scan_limit)
    retained = _RetainedBudget()
    retained.charge(256)
    if reader.read_exact(4) != GGUF_MAGIC:
        raise InvalidGGUFError("GGUF magic is invalid")

    raw_version = reader.read_exact(4)
    little_version = struct.unpack("<I", raw_version)[0]
    big_version = struct.unpack(">I", raw_version)[0]
    if little_version in SUPPORTED_VERSIONS:
        reader.endian = "<"
        version = little_version
    elif big_version in SUPPORTED_VERSIONS:
        raise InvalidGGUFError("Byte-swapped GGUF endianness is unsupported by this runtime")
    else:
        raise InvalidGGUFError(
            f"Unsupported GGUF version {little_version}; expected version 2 or 3"
        )

    tensor_count = int(reader.unpack("Q"))
    metadata_count = int(reader.unpack("Q"))
    if metadata_count > MAX_METADATA_ENTRIES:
        raise GGUFLimitError(f"GGUF metadata count exceeds the {MAX_METADATA_ENTRIES}-entry limit")
    if tensor_count > MAX_TENSOR_DESCRIPTORS:
        raise GGUFLimitError(
            f"GGUF tensor count exceeds the {MAX_TENSOR_DESCRIPTORS}-descriptor limit"
        )

    values: dict[str, GGUFValue] = {}
    seen_keys: set[str] = set()
    for _ in range(metadata_count):
        key = reader.read_string(keep=True, length_limit=MAX_KEY_BYTES)
        assert key is not None
        retained.charge(_retained_text_size(key))
        if key in seen_keys:
            raise InvalidGGUFError(f"GGUF metadata key is duplicated: {key!r}")
        seen_keys.add(key)
        keep = _selected_metadata_key(key)
        value_type = int(reader.unpack("I"))
        expected_type = _expected_integer_type(key)
        if expected_type is not None and value_type != expected_type:
            raise InvalidGGUFError(
                f"GGUF metadata {key!r} must use scalar type {expected_type}, not {value_type}"
            )
        value = reader.read_value(
            value_type,
            keep=keep,
            retained_budget=retained if keep else None,
        )
        if keep and value is not None:
            values[key] = value

    alignment_value = values.get("general.alignment", DEFAULT_ALIGNMENT)
    if (
        not isinstance(alignment_value, int)
        or isinstance(alignment_value, bool)
        or alignment_value <= 0
        or alignment_value > MAX_ALIGNMENT
        or alignment_value & (alignment_value - 1)
    ):
        raise InvalidGGUFError(
            f"GGUF general.alignment must be a positive power of two no larger than {MAX_ALIGNMENT}"
        )
    alignment = alignment_value

    selected_tensors: list[TensorDescriptor] = []
    seen_tensor_names: set[str] = set()
    tensor_payloads: list[tuple[int, int]] = []
    expected_offset = 0
    for _ in range(tensor_count):
        name = reader.read_string(keep=True, length_limit=MAX_TENSOR_NAME_BYTES)
        assert name is not None
        retained.charge(_retained_text_size(name))
        if name in seen_tensor_names:
            raise InvalidGGUFError(f"GGUF tensor name is duplicated: {name!r}")
        seen_tensor_names.add(name)
        dimension_count = int(reader.unpack("I"))
        if dimension_count == 0 or dimension_count > MAX_TENSOR_DIMS:
            raise GGUFLimitError(
                f"GGUF tensor dimension count must be between 1 and {MAX_TENSOR_DIMS}"
            )
        dimensions: list[int] = []
        elements = 1
        for _ in range(dimension_count):
            dimension = int(reader.unpack("Q"))
            if dimension == 0 or dimension > MAX_TENSOR_DIMENSION:
                raise GGUFLimitError(
                    f"GGUF tensor dimension exceeds the {MAX_TENSOR_DIMENSION} limit"
                )
            if elements > MAX_TENSOR_ELEMENTS // dimension:
                raise GGUFLimitError("GGUF tensor element count exceeds the inspection limit")
            elements *= dimension
            dimensions.append(dimension)
        tensor_type = int(reader.unpack("I"))
        offset = int(reader.unpack("Q"))
        dimension_tuple = tuple(dimensions)
        tensor_size = _tensor_nbytes(dimension_tuple, tensor_type)
        if offset != expected_offset:
            raise InvalidGGUFError("GGUF tensor offsets are not sequential and alignment-correct")
        tensor_payloads.append((offset, tensor_size))
        expected_offset = _align(offset + tensor_size, alignment)
        if _selected_tensor_name(name):
            if len(selected_tensors) >= MAX_SELECTED_TENSORS:
                raise GGUFLimitError(
                    f"GGUF has more than {MAX_SELECTED_TENSORS} selected tensor descriptors"
                )
            retained.charge(64 + 8 * len(dimensions))
            selected_tensors.append(
                TensorDescriptor(
                    name=name,
                    dimensions=dimension_tuple,
                    tensor_type=tensor_type,
                    offset=offset,
                )
            )

    descriptor_end = reader.offset
    data_offset = _align(descriptor_end, alignment)
    if data_offset > scan_limit:
        raise GGUFLimitError(f"GGUF header exceeds the {scan_limit}-byte inspection limit")
    if data_offset > file_size:
        raise InvalidGGUFError("GGUF tensor data section starts outside the file")

    data_size = 0
    for offset, tensor_size in tensor_payloads:
        payload_end = offset + tensor_size
        if payload_end > file_size - data_offset:
            raise InvalidGGUFError("GGUF tensor payload is truncated")
        data_size = max(data_size, payload_end)

    return GGUFMetadata(
        path=path,
        version=version,
        metadata_count=metadata_count,
        tensor_count=tensor_count,
        values=MappingProxyType(values),
        tensors=tuple(selected_tensors),
        alignment=alignment,
        data_offset=data_offset,
        data_size=data_size,
        scanned_bytes=data_offset,
        retained_bytes=retained.used,
    )


_StatSignature: TypeAlias = tuple[int, int, int, int, int]
_CacheKey: TypeAlias = tuple[str, _StatSignature]
_CACHE: OrderedDict[_CacheKey, GGUFMetadata] = OrderedDict()
_CACHE_LOCK = threading.Lock()
_CACHE_BYTES = 0


def _stat_signature(stat: os.stat_result) -> _StatSignature:
    return (
        int(stat.st_dev),
        int(stat.st_ino),
        int(stat.st_size),
        int(stat.st_mtime_ns),
        int(stat.st_ctime_ns),
    )


def clear_gguf_metadata_cache() -> None:
    """Clear the small process-local metadata cache."""

    global _CACHE_BYTES
    with _CACHE_LOCK:
        _CACHE.clear()
        _CACHE_BYTES = 0


def _cache_weight(metadata: GGUFMetadata) -> int:
    return 256 + metadata.retained_bytes


def read_gguf_metadata(
    path: str | os.PathLike[str],
    *,
    scan_limit: int = MAX_HEADER_BYTES,
) -> GGUFMetadata:
    """Inspect selected GGUF v2/v3 header facts without reading tensor payloads."""

    if (
        not isinstance(scan_limit, int)
        or isinstance(scan_limit, bool)
        or scan_limit <= 0
        or scan_limit > MAX_HEADER_BYTES
    ):
        raise GGUFLimitError(f"GGUF scan limit must be between 1 and {MAX_HEADER_BYTES} bytes")

    requested = Path(path).expanduser()
    try:
        resolved = requested.resolve(strict=True)
        before = resolved.stat()
    except OSError as exc:
        raise GGUFMetadataError("GGUF file is unavailable") from exc
    if not resolved.is_file():
        raise GGUFMetadataError("GGUF path is not a file")

    signature = _stat_signature(before)
    cache_key = (os.path.normcase(str(resolved)), signature)
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        if cached is not None:
            if cached.scanned_bytes > scan_limit:
                raise GGUFLimitError(f"GGUF header exceeds the {scan_limit}-byte inspection limit")
            _CACHE.move_to_end(cache_key)
            return cached

    try:
        with resolved.open("rb") as handle:
            opened_signature = _stat_signature(os.fstat(handle.fileno()))
            if opened_signature != signature:
                raise GGUFChangedError("GGUF file changed before inspection")
            try:
                result = _parse_file(
                    handle,
                    resolved,
                    file_size=before.st_size,
                    scan_limit=scan_limit,
                )
            except GGUFMetadataError as exc:
                exc.scanned_bytes = max(exc.scanned_bytes, handle.tell())
                raise
            except OSError as exc:
                unreadable = GGUFMetadataError("GGUF header could not be read")
                try:
                    unreadable.scanned_bytes = handle.tell()
                except OSError:
                    pass
                raise unreadable from exc
            after_handle = _stat_signature(os.fstat(handle.fileno()))
    except GGUFMetadataError:
        raise
    except OSError as exc:
        raise GGUFMetadataError("GGUF header could not be read") from exc

    try:
        after_path = _stat_signature(resolved.stat())
    except OSError as exc:
        changed = GGUFChangedError("GGUF file disappeared during inspection")
        changed.scanned_bytes = result.scanned_bytes
        raise changed from exc
    if after_handle != signature or after_path != signature:
        changed = GGUFChangedError("GGUF file changed during inspection")
        changed.scanned_bytes = result.scanned_bytes
        raise changed

    global _CACHE_BYTES
    with _CACHE_LOCK:
        existing = _CACHE.get(cache_key)
        if existing is not None:
            if existing.scanned_bytes > scan_limit:
                raise GGUFLimitError(f"GGUF header exceeds the {scan_limit}-byte inspection limit")
            _CACHE.move_to_end(cache_key)
            return existing
        weight = _cache_weight(result)
        if weight <= METADATA_CACHE_BYTE_BUDGET:
            _CACHE[cache_key] = result
            _CACHE_BYTES += weight
        while len(_CACHE) > METADATA_CACHE_SIZE or _CACHE_BYTES > METADATA_CACHE_BYTE_BUDGET:
            _, evicted = _CACHE.popitem(last=False)
            _CACHE_BYTES -= _cache_weight(evicted)
    return result
