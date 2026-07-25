from __future__ import annotations

import struct
from pathlib import Path

import pytest

from models import gguf_metadata
from models import projectors as projector_module
from models.catalog import ModelCatalog
from models.gguf_metadata import clear_gguf_metadata_cache
from models.projectors import (
    AUTO_PROJECTOR,
    NONE_PROJECTOR,
    ProjectorResolutionError,
    resolve_direct_projector,
)

_UINT32 = 4
_INT32 = 5
_BOOL = 7
_STRING = 8
_ARRAY = 9


def _string(value: str) -> bytes:
    encoded = value.encode()
    return struct.pack("<Q", len(encoded)) + encoded


def _value(value: object) -> tuple[int, bytes]:
    if isinstance(value, bool):
        return _BOOL, struct.pack("<B", int(value))
    if isinstance(value, int):
        return _UINT32, struct.pack("<I", value)
    if isinstance(value, str):
        return _STRING, _string(value)
    if isinstance(value, (list, tuple)):
        if not value:
            element_type = _STRING
            payload = b""
        elif all(isinstance(item, bool) for item in value):
            element_type = _BOOL
            payload = b"".join(struct.pack("<B", int(item)) for item in value)
        elif all(isinstance(item, str) for item in value):
            element_type = _STRING
            payload = b"".join(_string(item) for item in value)
        else:
            raise TypeError(value)
        return _ARRAY, struct.pack("<IQ", element_type, len(value)) + payload
    raise TypeError(value)


def write_gguf(
    path: Path,
    metadata: dict[str, object],
    tensors: list[tuple[str, tuple[int, ...]]] | None = None,
    *,
    add_required_output: bool = True,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tensors = list(tensors or [])
    projector_type = str(
        metadata.get("clip.projector_type") or metadata.get("clip.vision.projector_type") or ""
    ).casefold()
    projection_dim = metadata.get("clip.vision.projection_dim")
    output_names = {"mm.2.bias", "mm.input_projection.weight"}
    if (
        add_required_output
        and isinstance(projection_dim, int)
        and not isinstance(projection_dim, bool)
        and not any(name in output_names for name, _ in tensors)
    ):
        if projector_type == "gemma3":
            tensors.append(("mm.input_projection.weight", (projection_dim, 1)))
        elif projector_type in {"gemma4v", "gemma4uv"}:
            tensors.append(("mm.input_projection.weight", (1, projection_dim)))
        elif projector_type == "qwen3vl_merger":
            tensors.append(("mm.2.bias", (projection_dim,)))
    alignment = gguf_metadata.DEFAULT_ALIGNMENT
    descriptors: list[tuple[str, tuple[int, ...], int, int]] = []
    expected_offset = 0
    for name, dimensions in tensors:
        tensor_type = 24  # I8 keeps sparse synthetic test files reasonably small.
        elements = 1
        for dimension in dimensions:
            elements *= dimension
        tensor_size = elements
        descriptors.append((name, dimensions, tensor_type, expected_offset))
        expected_offset = (expected_offset + tensor_size + alignment - 1) & -alignment

    body = bytearray(b"GGUF")
    body.extend(struct.pack("<IQQ", 3, len(descriptors), len(metadata)))
    for key, value in metadata.items():
        value_type, payload = _value(value)
        if (
            key == "clip.minicpmv_version"
            and isinstance(value, int)
            and not isinstance(value, bool)
        ):
            value_type = _INT32
            payload = struct.pack("<i", value)
        body.extend(_string(key))
        body.extend(struct.pack("<I", value_type))
        body.extend(payload)
    for name, dimensions, tensor_type, offset in descriptors:
        body.extend(_string(name))
        body.extend(struct.pack("<I", len(dimensions)))
        body.extend(struct.pack("<" + ("Q" * len(dimensions)), *dimensions))
        body.extend(struct.pack("<IQ", tensor_type, offset))
    data_offset = (len(body) + alignment - 1) & -alignment
    body.extend(b"\x00" * (data_offset - len(body)))
    with path.open("wb") as handle:
        handle.write(body)
        if descriptors:
            handle.truncate(
                data_offset
                + max(
                    offset + _tensor_nbytes(dimensions) for _, dimensions, _, offset in descriptors
                )
            )
    return path


def _tensor_nbytes(dimensions: tuple[int, ...]) -> int:
    elements = 1
    for dimension in dimensions:
        elements *= dimension
    return elements


def model_metadata(
    architecture: str,
    name: str,
    width: int,
    *,
    tags: list[str] | None = None,
    deepstack: int = 0,
    base_name: str | None = None,
    base_repo: str | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "general.architecture": architecture,
        "general.type": "model",
        "general.name": name,
        f"{architecture}.embedding_length": width,
    }
    if tags is not None:
        result["general.tags"] = tags
    if deepstack:
        result[f"{architecture}.n_deepstack_layers"] = deepstack
    if base_name or base_repo:
        result["general.base_model.count"] = 1
    if base_name:
        result["general.base_model.0.name"] = base_name
    if base_repo:
        result["general.base_model.0.repo_url"] = base_repo
    return result


def projector_metadata(
    projector_type: str,
    name: str,
    width: int,
    *,
    deepstack: int = 0,
    base_name: str | None = None,
    base_repo: str | None = None,
    file_type: int = 1,
) -> dict[str, object]:
    result: dict[str, object] = {
        "general.architecture": "clip",
        "general.type": "mmproj",
        "general.name": name,
        "general.file_type": file_type,
        "clip.has_vision_encoder": True,
        "clip.projector_type": projector_type,
        "clip.vision.projection_dim": width,
    }
    if deepstack:
        true_indices = {index * 6 + 5 for index in range(deepstack)}
        result["clip.vision.is_deepstack_layers"] = [
            index in true_indices for index in range(max(true_indices) + 1)
        ]
    if base_name or base_repo:
        result["general.base_model.count"] = 1
    if base_name:
        result["general.base_model.0.name"] = base_name
    if base_repo:
        result["general.base_model.0.repo_url"] = base_repo
    return result


def deepstack_tensors(count: int, width: int) -> list[tuple[str, tuple[int, ...]]]:
    tensors: list[tuple[str, tuple[int, ...]]] = []
    hidden = width * 2
    for index in range(count):
        layer = index * 6 + 5
        tensors.extend(
            (
                (f"v.deepstack.{layer}.fc1.weight", (hidden, hidden)),
                (f"v.deepstack.{layer}.fc2.weight", (hidden, width)),
            )
        )
    return tensors


@pytest.fixture(autouse=True)
def _empty_metadata_cache():
    clear_gguf_metadata_cache()
    yield
    clear_gguf_metadata_cache()


def test_constants_and_status_shape_are_stable(tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"not parsed in explicit text-only mode")

    result = resolve_direct_projector(
        "model.gguf",
        NONE_PROJECTOR,
        catalog=ModelCatalog([tmp_path]),
    )

    assert AUTO_PROJECTOR == "(auto)"
    assert NONE_PROJECTOR == "(none - text only)"
    assert result.model_name == "model.gguf"
    assert result.model_path == model
    assert result.projector_path is None
    assert result.status_dict() == {
        "mode": "none",
        "outcome": "text_only",
        "detail": "explicit text-only selection",
    }


def test_legacy_blank_selection_remains_intentional_text_only(tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"not parsed for a legacy blank selection")

    result = resolve_direct_projector(
        "model.gguf",
        "",
        catalog=ModelCatalog([tmp_path]),
    )

    assert result.mode == "none"
    assert result.outcome == "text_only"
    assert result.status_dict() == {
        "mode": "none",
        "outcome": "text_only",
        "detail": "legacy blank text-only selection",
    }


def test_concrete_selection_is_exact_and_bypasses_all_metadata(tmp_path):
    model = tmp_path / "bundle" / "model.gguf"
    projector = tmp_path / "bundle" / "mmproj-corrupt.gguf"
    model.parent.mkdir()
    model.write_bytes(b"corrupt model")
    projector.write_bytes(b"corrupt projector")

    result = resolve_direct_projector(
        "bundle\\model.gguf",
        "bundle/mmproj-corrupt.gguf",
        catalog=ModelCatalog([tmp_path]),
    )

    assert result.mode == "explicit"
    assert result.outcome == "explicit"
    assert result.projector_name == "bundle/mmproj-corrupt.gguf"
    assert result.projector_path == projector
    assert result.status_dict() == {
        "mode": "explicit",
        "outcome": "selected",
        "projector": "bundle/mmproj-corrupt.gguf",
        "detail": "exact manual projector selection",
    }


@pytest.mark.parametrize(
    ("architecture", "projector_type", "name", "width"),
    [
        ("gemma3", "gemma3", "Gemma 3 12B It", 3840),
        ("gemma4", "gemma4v", "Gemma 4 26B A4B It", 2816),
        ("qwen35", "qwen3vl_merger", "Qwen3.5 27B", 5120),
    ],
)
def test_modern_metadata_families_auto_select(tmp_path, architecture, projector_type, name, width):
    bundle = tmp_path / architecture
    write_gguf(
        bundle / f"{name}-Q6_K.gguf",
        model_metadata(
            architecture,
            f"{name} derivative",
            width,
            tags=["image-text-to-text"],
            base_name=name,
        ),
    )
    projector = write_gguf(
        bundle / f"{name}-mmproj-Q8_0.gguf",
        projector_metadata(
            projector_type,
            name,
            width,
            base_name=name,
            file_type=7,
        ),
    )

    result = resolve_direct_projector(
        f"{architecture}/{name}-Q6_K.gguf",
        AUTO_PROJECTOR,
        catalog=ModelCatalog([tmp_path]),
    )

    assert result.outcome == "auto_selected"
    assert result.projector_path == projector
    assert any(item.startswith("family=") for item in result.evidence)


@pytest.mark.parametrize(
    ("architecture", "projector_type", "name", "width", "deepstack", "wrong_output"),
    [
        (
            "gemma3",
            "gemma3",
            "Gemma 3 12B It",
            3840,
            0,
            ("mm.input_projection.weight", (2048, 1)),
        ),
        (
            "gemma4",
            "gemma4v",
            "Gemma 4 26B A4B It",
            2816,
            0,
            ("mm.input_projection.weight", (1, 2048)),
        ),
        (
            "qwen3vl",
            "qwen3vl_merger",
            "Qwen3 VL 4B Instruct",
            2560,
            1,
            ("mm.2.bias", (2048,)),
        ),
    ],
)
def test_modern_output_tensor_width_must_agree_with_metadata(
    tmp_path,
    architecture,
    projector_type,
    name,
    width,
    deepstack,
    wrong_output,
):
    bundle = tmp_path / architecture
    write_gguf(
        bundle / f"{name}-Q6_K.gguf",
        model_metadata(
            architecture,
            name,
            width,
            tags=["image-text-to-text"],
            deepstack=deepstack,
        ),
    )
    tensors = deepstack_tensors(deepstack, width)
    tensors.append(wrong_output)
    write_gguf(
        bundle / f"{name}-mmproj-f16.gguf",
        projector_metadata(
            projector_type,
            name,
            width,
            deepstack=deepstack,
        ),
        tensors,
    )

    with pytest.raises(ProjectorResolutionError, match="No compatible installed projector"):
        resolve_direct_projector(
            f"{architecture}/{name}-Q6_K.gguf",
            AUTO_PROJECTOR,
            catalog=ModelCatalog([tmp_path]),
        )


def test_modern_projector_requires_the_pinned_output_tensor(tmp_path):
    bundle = tmp_path / "qwen"
    width = 2560
    name = "Qwen3 VL 4B Instruct"
    write_gguf(
        bundle / "Qwen3-VL-4B-Instruct-Q6_K.gguf",
        model_metadata(
            "qwen3vl",
            name,
            width,
            tags=["image-text-to-text"],
            deepstack=1,
        ),
    )
    write_gguf(
        bundle / "mmproj-Qwen3-VL-4B-Instruct-f16.gguf",
        projector_metadata(
            "qwen3vl_merger",
            name,
            width,
            deepstack=1,
        ),
        deepstack_tensors(1, width),
        add_required_output=False,
    )

    with pytest.raises(ProjectorResolutionError, match="inconclusive"):
        resolve_direct_projector(
            "qwen/Qwen3-VL-4B-Instruct-Q6_K.gguf",
            AUTO_PROJECTOR,
            catalog=ModelCatalog([tmp_path]),
        )


def test_qwen_deepstack_compares_effective_interfaces(tmp_path):
    bundle = tmp_path / "qwen"
    write_gguf(
        bundle / "Qwen3-VL-4B-Instruct-Q6_K.gguf",
        model_metadata(
            "qwen3vl",
            "Qwen3 VL 4B Instruct",
            2560,
            tags=["image-text-to-text"],
            deepstack=3,
        ),
    )
    projector = write_gguf(
        bundle / "mmproj-Qwen3-VL-4B-Instruct-f16.gguf",
        projector_metadata(
            "qwen3vl_merger",
            "Qwen3 VL 4B Instruct",
            2560,
            deepstack=3,
        ),
        deepstack_tensors(3, 2560),
    )

    result = resolve_direct_projector(
        "qwen/Qwen3-VL-4B-Instruct-Q6_K.gguf",
        AUTO_PROJECTOR,
        catalog=ModelCatalog([tmp_path]),
    )

    assert result.projector_path == projector
    assert "interface=10240" in result.evidence


def test_per_layer_embedding_metadata_does_not_replace_the_vlm_input_width(tmp_path):
    bundle = tmp_path / "qwen"
    metadata = model_metadata(
        "qwen3vl",
        "Qwen3 VL 4B Instruct",
        2560,
        tags=["image-text-to-text"],
        deepstack=3,
    )
    metadata["qwen3vl.embedding_length_per_layer_input"] = 2816
    write_gguf(
        bundle / "Qwen3-VL-4B-Instruct-Q6_K.gguf",
        metadata,
    )
    projector = write_gguf(
        bundle / "mmproj-Qwen3-VL-4B-Instruct-f16.gguf",
        projector_metadata(
            "qwen3vl_merger",
            "Qwen3 VL 4B Instruct",
            2560,
            deepstack=3,
        ),
        deepstack_tensors(3, 2560),
    )

    result = resolve_direct_projector(
        "qwen/Qwen3-VL-4B-Instruct-Q6_K.gguf",
        AUTO_PROJECTOR,
        catalog=ModelCatalog([tmp_path]),
    )

    assert result.projector_path == projector
    assert "interface=10240" in result.evidence


def test_qwen_deepstack_conflict_and_same_raw_width_do_not_authorize(tmp_path):
    bundle = tmp_path / "qwen"
    write_gguf(
        bundle / "Qwen3-VL-4B-Instruct-Q6_K.gguf",
        model_metadata(
            "qwen3vl",
            "Qwen3 VL 4B Instruct",
            2560,
            tags=["image-text-to-text"],
            deepstack=3,
        ),
    )
    write_gguf(
        bundle / "mmproj-Qwen3-VL-4B-Instruct-f16.gguf",
        projector_metadata(
            "qwen3vl_merger",
            "Qwen3 VL 4B Instruct",
            2560,
            deepstack=2,
        ),
        deepstack_tensors(3, 2560),
    )

    with pytest.raises(ProjectorResolutionError, match="inconclusive"):
        resolve_direct_projector(
            "qwen/Qwen3-VL-4B-Instruct-Q6_K.gguf",
            AUTO_PROJECTOR,
            catalog=ModelCatalog([tmp_path]),
        )


@pytest.mark.parametrize(
    "tensor_variant",
    ["absent", "fc2-only", "wrong-shape", "wrong-index", "malformed-flags"],
)
def test_qwen_deepstack_requires_exact_paired_tensor_graph(tmp_path, tensor_variant):
    bundle = tmp_path / tensor_variant
    width = 2560
    valid = deepstack_tensors(1, width)
    if tensor_variant == "absent":
        tensors: list[tuple[str, tuple[int, ...]]] = []
    elif tensor_variant == "fc2-only":
        tensors = [valid[1]]
    elif tensor_variant == "wrong-shape":
        tensors = [valid[0], (valid[1][0], (width * 2, width + 1))]
    elif tensor_variant == "wrong-index":
        tensors = [
            ("v.deepstack.6.fc1.weight", (width * 2, width * 2)),
            ("v.deepstack.6.fc2.weight", (width * 2, width)),
        ]
    else:
        tensors = valid

    write_gguf(
        bundle / "Qwen3-VL-4B-Instruct-Q6_K.gguf",
        model_metadata(
            "qwen3vl",
            "Qwen3 VL 4B Instruct",
            width,
            tags=["image-text-to-text"],
            deepstack=1,
        ),
    )
    metadata = projector_metadata(
        "qwen3vl_merger",
        "Qwen3 VL 4B Instruct",
        width,
        deepstack=1,
    )
    if tensor_variant == "malformed-flags":
        metadata["clip.vision.is_deepstack_layers"] = "not-a-boolean-array"
    write_gguf(
        bundle / "mmproj-Qwen3-VL-4B-Instruct-f16.gguf",
        metadata,
        tensors,
    )

    with pytest.raises(ProjectorResolutionError, match="inconclusive"):
        resolve_direct_projector(
            f"{tensor_variant}/Qwen3-VL-4B-Instruct-Q6_K.gguf",
            AUTO_PROJECTOR,
            catalog=ModelCatalog([tmp_path]),
        )


def test_conflicting_projector_type_fields_fail_closed(tmp_path):
    bundle = tmp_path / "bundle"
    write_gguf(
        bundle / "Gemma-3-12B-It-Q6_K.gguf",
        model_metadata("gemma3", "Gemma 3 12B It", 3840),
    )
    metadata = projector_metadata("gemma3", "Gemma 3 12B It", 3840)
    metadata["clip.vision.projector_type"] = "qwen3vl_merger"
    write_gguf(
        bundle / "Gemma-3-12B-It.mmproj-f16.gguf",
        metadata,
    )

    with pytest.raises(ProjectorResolutionError, match="inconclusive"):
        resolve_direct_projector(
            "bundle/Gemma-3-12B-It-Q6_K.gguf",
            AUTO_PROJECTOR,
            catalog=ModelCatalog([tmp_path]),
        )


def test_mixed_gemma4_directory_selects_only_matching_size(tmp_path):
    bundle = tmp_path / "gemma-4"
    write_gguf(
        bundle / "gemma-4-26B-A4B-it.Q6_K.gguf",
        model_metadata(
            "gemma4",
            "Gemma 4 26B A4B It",
            2816,
            tags=["image-text-to-text"],
        ),
    )
    matching = write_gguf(
        bundle / "gemma-4-26B-A4B-it.mmproj-Q8_0.gguf",
        projector_metadata("gemma4v", "Gemma 4 26B A4B It", 2816, file_type=7),
    )
    write_gguf(
        bundle / "gemma-4-31B-it.mmproj-f16.gguf",
        projector_metadata("gemma4v", "Gemma 4 31B It", 5376),
    )

    result = resolve_direct_projector(
        "gemma-4/gemma-4-26B-A4B-it.Q6_K.gguf",
        AUTO_PROJECTOR,
        catalog=ModelCatalog([tmp_path]),
    )

    assert result.projector_path == matching


def test_unique_adjacent_wrong_dimension_does_not_authorize(tmp_path):
    bundle = tmp_path / "gemma-4"
    write_gguf(
        bundle / "gemma-4-31B-it.Q6_K.gguf",
        model_metadata(
            "gemma4",
            "Gemma 4 31B It",
            5376,
            tags=["image-text-to-text"],
        ),
    )
    write_gguf(
        bundle / "gemma-4-31B-it.mmproj-f16.gguf",
        projector_metadata("gemma4v", "Gemma 4 31B It", 2816),
    )

    with pytest.raises(ProjectorResolutionError, match="No compatible installed projector"):
        resolve_direct_projector(
            "gemma-4/gemma-4-31B-it.Q6_K.gguf",
            AUTO_PROJECTOR,
            catalog=ModelCatalog([tmp_path]),
        )


def test_global_match_requires_strong_base_lineage(tmp_path):
    models = tmp_path / "models"
    projectors = tmp_path / "projectors"
    repo = "https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct"
    write_gguf(
        models / "Qwen3-VL-8B-derivative-Q6_K.gguf",
        model_metadata(
            "qwen3vl",
            "Different derivative name",
            4096,
            tags=["image-text-to-text"],
            deepstack=3,
            base_repo=repo,
        ),
    )
    projector = write_gguf(
        projectors / "nested" / "mmproj-unrelated-filename-Q8_0.gguf",
        projector_metadata(
            "qwen3vl_merger",
            "Upstream projector identity",
            4096,
            deepstack=3,
            base_repo=repo.upper(),
            file_type=7,
        ),
        deepstack_tensors(3, 4096),
    )

    result = resolve_direct_projector(
        "Qwen3-VL-8B-derivative-Q6_K.gguf",
        AUTO_PROJECTOR,
        catalog=ModelCatalog([models, projectors]),
    )

    assert result.projector_path == projector
    assert "catalog-wide identity match" in result.evidence


def test_credentialed_lineage_repo_never_authorizes_or_leaks(tmp_path):
    models = tmp_path / "models"
    projectors = tmp_path / "projectors"
    credentialed_repo = "https://alice:supersecret@huggingface.co/Qwen/Qwen3-VL-4B-Instruct"
    write_gguf(
        models / "local-Q6_K.gguf",
        model_metadata(
            "qwen3vl",
            "Local derivative",
            2560,
            tags=["image-text-to-text"],
            deepstack=1,
            base_repo=credentialed_repo,
        ),
    )
    write_gguf(
        projectors / "mmproj-upstream-f16.gguf",
        projector_metadata(
            "qwen3vl_merger",
            "Different upstream projector",
            2560,
            deepstack=1,
            base_repo=credentialed_repo,
        ),
        deepstack_tensors(1, 2560),
    )

    with pytest.raises(ProjectorResolutionError) as caught:
        resolve_direct_projector(
            "local-Q6_K.gguf",
            AUTO_PROJECTOR,
            catalog=ModelCatalog([models, projectors]),
        )

    rendered = str(caught.value)
    assert "alice" not in rendered
    assert "supersecret" not in rendered


def test_credentialed_lineage_repo_is_never_retained_in_safe_result(tmp_path):
    models = tmp_path / "models"
    projectors = tmp_path / "projectors"
    credentialed_repo = "https://alice:supersecret@huggingface.co/Qwen/Qwen3-VL-4B-Instruct"
    write_gguf(
        models / "local-Q6_K.gguf",
        model_metadata(
            "qwen3vl",
            "Shared safe identity",
            2560,
            tags=["image-text-to-text"],
            deepstack=1,
            base_repo=credentialed_repo,
        ),
    )
    projector = write_gguf(
        projectors / "mmproj-upstream-f16.gguf",
        projector_metadata(
            "qwen3vl_merger",
            "Shared safe identity",
            2560,
            deepstack=1,
            base_repo=credentialed_repo,
        ),
        deepstack_tensors(1, 2560),
    )

    result = resolve_direct_projector(
        "local-Q6_K.gguf",
        AUTO_PROJECTOR,
        catalog=ModelCatalog([models, projectors]),
    )

    assert result.projector_path == projector
    rendered = repr((result.evidence, result.status_dict()))
    assert "alice" not in rendered
    assert "supersecret" not in rendered


def test_malformed_ipv6_lineage_repo_is_ignored_as_identity_evidence(tmp_path):
    models = tmp_path / "models"
    projectors = tmp_path / "projectors"
    malformed_repo = "https://[bad/owner/repo"
    write_gguf(
        models / "local-Q6_K.gguf",
        model_metadata(
            "qwen3vl",
            "Local derivative",
            2560,
            tags=["image-text-to-text"],
            deepstack=1,
            base_repo=malformed_repo,
        ),
    )
    write_gguf(
        projectors / "mmproj-upstream-f16.gguf",
        projector_metadata(
            "qwen3vl_merger",
            "Different upstream projector",
            2560,
            deepstack=1,
            base_repo=malformed_repo,
        ),
        deepstack_tensors(1, 2560),
    )

    with pytest.raises(ProjectorResolutionError, match="No compatible installed projector"):
        resolve_direct_projector(
            "local-Q6_K.gguf",
            AUTO_PROJECTOR,
            catalog=ModelCatalog([models, projectors]),
        )


@pytest.mark.parametrize(
    ("lineage_count", "lineage_index"),
    [
        (None, 0),
        (0, 0),
        (1, 1),
        (projector_module.MAX_BASE_MODELS + 1, 0),
    ],
)
def test_invalid_or_out_of_range_lineage_never_authorizes_global_pairing(
    tmp_path,
    lineage_count,
    lineage_index,
):
    models = tmp_path / "models"
    projectors = tmp_path / "projectors"
    repo = "https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct"
    model = model_metadata(
        "qwen3vl",
        "Local derivative",
        2560,
        tags=["image-text-to-text"],
        deepstack=1,
    )
    if lineage_count is not None:
        model["general.base_model.count"] = lineage_count
    model[f"general.base_model.{lineage_index}.repo_url"] = repo
    write_gguf(models / "local-Q6_K.gguf", model)
    write_gguf(
        projectors / "mmproj-upstream-f16.gguf",
        projector_metadata(
            "qwen3vl_merger",
            "Upstream projector",
            2560,
            deepstack=1,
            base_repo=repo,
        ),
        deepstack_tensors(1, 2560),
    )

    with pytest.raises(ProjectorResolutionError, match="No compatible installed projector"):
        resolve_direct_projector(
            "local-Q6_K.gguf",
            AUTO_PROJECTOR,
            catalog=ModelCatalog([models, projectors]),
        )


def test_malformed_lineage_count_never_authorizes_global_pairing(tmp_path):
    models = tmp_path / "models"
    projectors = tmp_path / "projectors"
    repo = "https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct"
    model = model_metadata(
        "qwen3vl",
        "Local derivative",
        2560,
        tags=["image-text-to-text"],
        deepstack=1,
    )
    model["general.base_model.count"] = "1"
    model["general.base_model.0.repo_url"] = repo
    write_gguf(models / "local-Q6_K.gguf", model)
    write_gguf(
        projectors / "mmproj-upstream-f16.gguf",
        projector_metadata(
            "qwen3vl_merger",
            "Upstream projector",
            2560,
            deepstack=1,
            base_repo=repo,
        ),
        deepstack_tensors(1, 2560),
    )

    result = resolve_direct_projector(
        "local-Q6_K.gguf",
        AUTO_PROJECTOR,
        catalog=ModelCatalog([models, projectors]),
    )

    assert result.outcome == "auto_text_only"
    assert result.projector_path is None


def test_equivalent_strong_global_candidate_participates_with_adjacent_candidate(tmp_path):
    local = tmp_path / "local"
    global_root = tmp_path / "global"
    base_name = "Qwen3 VL 4B Instruct"
    write_gguf(
        local / "Derivative-Q6_K.gguf",
        model_metadata(
            "qwen3vl",
            "Qwen3 VL 4B Instruct Abliterated",
            2560,
            tags=["image-text-to-text"],
            deepstack=3,
            base_name=base_name,
        ),
    )
    write_gguf(
        local / "Derivative-mmproj-f16.gguf",
        projector_metadata(
            "qwen3vl_merger",
            "Qwen3 VL 4B Instruct Abliterated",
            2560,
            deepstack=3,
            base_name=base_name,
            file_type=1,
        ),
        deepstack_tensors(3, 2560),
    )
    preferred = write_gguf(
        global_root / "mmproj-upstream-Q8_0.gguf",
        projector_metadata(
            "qwen3vl_merger",
            base_name,
            2560,
            deepstack=3,
            base_name=base_name,
            file_type=7,
        ),
        deepstack_tensors(3, 2560),
    )

    result = resolve_direct_projector(
        "Derivative-Q6_K.gguf",
        AUTO_PROJECTOR,
        catalog=ModelCatalog([local, global_root]),
    )

    assert result.projector_path == preferred
    assert "preferred quantization=Q8_0" in result.evidence


def test_adjacent_exact_normalized_filename_identity_can_match(tmp_path):
    bundle = tmp_path / "bundle"
    write_gguf(
        bundle / "Qwen_Qwen3-VL-4B-Instruct-Q6_K.gguf",
        model_metadata(
            "qwen3vl",
            "Derivative with no shared metadata name",
            2560,
            tags=["image-text-to-text"],
            deepstack=3,
        ),
    )
    projector = write_gguf(
        bundle / "mmproj-Qwen_Qwen3-VL-4B-Instruct-f16.gguf",
        projector_metadata(
            "qwen3vl_merger",
            "Different projector metadata name",
            2560,
            deepstack=3,
        ),
        deepstack_tensors(3, 2560),
    )

    result = resolve_direct_projector(
        "bundle/Qwen_Qwen3-VL-4B-Instruct-Q6_K.gguf",
        AUTO_PROJECTOR,
        catalog=ModelCatalog([tmp_path]),
    )

    assert result.projector_path == projector
    assert any("normalized filename" in item for item in result.evidence)


def test_global_filename_identity_alone_cannot_authorize(tmp_path):
    models = tmp_path / "models"
    projectors = tmp_path / "projectors"
    write_gguf(
        models / "Qwen_Qwen3-VL-4B-Instruct-Q6_K.gguf",
        model_metadata(
            "qwen3vl",
            "Derivative with no shared metadata name",
            2560,
            tags=["image-text-to-text"],
            deepstack=3,
        ),
    )
    write_gguf(
        projectors / "mmproj-Qwen_Qwen3-VL-4B-Instruct-f16.gguf",
        projector_metadata(
            "qwen3vl_merger",
            "Different projector metadata name",
            2560,
            deepstack=3,
        ),
        deepstack_tensors(3, 2560),
    )

    with pytest.raises(ProjectorResolutionError, match="No compatible installed projector"):
        resolve_direct_projector(
            "Qwen_Qwen3-VL-4B-Instruct-Q6_K.gguf",
            AUTO_PROJECTOR,
            catalog=ModelCatalog([models, projectors]),
        )


def test_corrupt_global_filename_match_is_not_a_plausible_candidate(tmp_path):
    models = tmp_path / "models"
    projectors = tmp_path / "projectors"
    write_gguf(
        models / "Gemma-3-12B-It-Q6_K.gguf",
        model_metadata("gemma3", "Gemma 3 12B It", 3840),
    )
    projectors.mkdir()
    (projectors / "Gemma-3-12B-It-mmproj-Q8_0.gguf").write_bytes(b"corrupt")

    with pytest.raises(ProjectorResolutionError, match="No compatible installed projector"):
        resolve_direct_projector(
            "Gemma-3-12B-It-Q6_K.gguf",
            AUTO_PROJECTOR,
            catalog=ModelCatalog([models, projectors]),
        )


def test_unreadable_model_does_not_treat_global_filename_as_plausible(tmp_path):
    models = tmp_path / "models"
    projectors = tmp_path / "projectors"
    (models / "target-Q6_K.gguf").parent.mkdir(parents=True)
    (models / "target-Q6_K.gguf").write_bytes(b"unreadable")
    write_gguf(
        projectors / "target-mmproj-Q8_0.gguf",
        projector_metadata("gemma3", "Target", 3840, file_type=7),
    )

    result = resolve_direct_projector(
        "target-Q6_K.gguf",
        AUTO_PROJECTOR,
        catalog=ModelCatalog([models, projectors]),
    )

    assert result.outcome == "auto_text_only"


def test_global_same_width_without_identity_is_not_a_match(tmp_path):
    models = tmp_path / "models"
    projectors = tmp_path / "projectors"
    write_gguf(
        models / "target.gguf",
        model_metadata(
            "gemma3",
            "Gemma 3 Target",
            3840,
            tags=["image-text-to-text"],
        ),
    )
    write_gguf(
        projectors / "mmproj-other.gguf",
        projector_metadata("gemma3", "Gemma 3 Other", 3840),
    )

    with pytest.raises(ProjectorResolutionError, match="No compatible installed projector"):
        resolve_direct_projector(
            "target.gguf",
            AUTO_PROJECTOR,
            catalog=ModelCatalog([models, projectors]),
        )


def test_global_exact_but_generic_metadata_name_is_not_identity(tmp_path):
    models = tmp_path / "models"
    projectors = tmp_path / "projectors"
    write_gguf(
        models / "target.gguf",
        model_metadata(
            "gemma3",
            "Model 4B",
            3840,
            tags=["image-text-to-text"],
        ),
    )
    write_gguf(
        projectors / "mmproj-other.gguf",
        projector_metadata("gemma3", "Model 4B", 3840),
    )

    with pytest.raises(ProjectorResolutionError, match="No compatible installed projector"):
        resolve_direct_projector(
            "target.gguf",
            AUTO_PROJECTOR,
            catalog=ModelCatalog([models, projectors]),
        )


def test_unresolved_adjacent_candidate_fails_instead_of_guessing(tmp_path):
    bundle = tmp_path / "bundle"
    write_gguf(
        bundle / "target.gguf",
        model_metadata(
            "gemma3",
            "Gemma 3 Target",
            3840,
            tags=["image-text-to-text"],
        ),
    )
    write_gguf(
        bundle / "mmproj-other.gguf",
        projector_metadata("gemma3", "Gemma 3 Other", 3840),
    )

    with pytest.raises(ProjectorResolutionError, match="unresolved plausible"):
        resolve_direct_projector(
            "bundle/target.gguf",
            AUTO_PROJECTOR,
            catalog=ModelCatalog([tmp_path]),
        )


def test_corrupt_adjacent_candidate_fails_closed(tmp_path):
    bundle = tmp_path / "bundle"
    write_gguf(
        bundle / "target.gguf",
        model_metadata(
            "gemma3",
            "Gemma 3 Target",
            3840,
            tags=["image-text-to-text"],
        ),
    )
    (bundle / "mmproj-target.gguf").write_bytes(b"corrupt")

    with pytest.raises(ProjectorResolutionError, match="inconclusive"):
        resolve_direct_projector(
            "bundle/target.gguf",
            AUTO_PROJECTOR,
            catalog=ModelCatalog([tmp_path]),
        )


def test_equivalent_quant_variants_prefer_q8_then_bf16_f16_f32(tmp_path):
    bundle = tmp_path / "bundle"
    write_gguf(
        bundle / "Gemma-3-12B-It-Q6_K.gguf",
        model_metadata("gemma3", "Gemma 3 12B It", 3840),
    )
    for suffix, file_type in [
        ("F32", 0),
        ("f16", 1),
        ("BF16", 32),
    ]:
        write_gguf(
            bundle / f"Gemma-3-12B-It.mmproj-{suffix}.gguf",
            projector_metadata("gemma3", "Gemma 3 12B It", 3840, file_type=file_type),
        )
    q8 = write_gguf(
        bundle / "Gemma-3-12B-It.mmproj-Q8_0.gguf",
        projector_metadata("gemma3", "Gemma 3 12B It", 3840, file_type=7),
    )

    result = resolve_direct_projector(
        "bundle/Gemma-3-12B-It-Q6_K.gguf",
        AUTO_PROJECTOR,
        catalog=ModelCatalog([tmp_path]),
    )

    assert result.projector_path == q8
    assert "preferred quantization=Q8_0" in result.evidence


def test_metadata_file_type_is_authoritative_for_quantization_ranking(tmp_path):
    bundle = tmp_path / "bundle"
    name = "Gemma 3 12B It"
    write_gguf(
        bundle / "model-Q6_K.gguf",
        model_metadata("gemma3", name, 3840),
    )
    write_gguf(
        bundle / "mmproj-alpha.gguf",
        projector_metadata("gemma3", name, 3840, file_type=1),
    )
    preferred = write_gguf(
        bundle / "mmproj-zulu.gguf",
        projector_metadata("gemma3", name, 3840, file_type=7),
    )

    result = resolve_direct_projector(
        "bundle/model-Q6_K.gguf",
        AUTO_PROJECTOR,
        catalog=ModelCatalog([tmp_path]),
    )

    assert result.projector_path == preferred
    assert "preferred quantization=Q8_0" in result.evidence


def test_quantization_fallback_uses_filename_not_parent_directory(tmp_path):
    bundle = tmp_path / "Q8_0"
    name = "Gemma 3 12B It"
    write_gguf(
        bundle / "model.gguf",
        model_metadata("gemma3", name, 3840),
    )
    projector = write_gguf(
        bundle / "mmproj-model.gguf",
        projector_metadata("gemma3", name, 3840, file_type=1),
    )

    result = resolve_direct_projector(
        "Q8_0/model.gguf",
        AUTO_PROJECTOR,
        catalog=ModelCatalog([tmp_path]),
    )

    assert result.projector_path == projector
    assert "preferred quantization=F16" in result.evidence


def test_filename_quantization_is_only_a_fallback_when_metadata_is_absent(tmp_path):
    bundle = tmp_path / "bundle"
    name = "Gemma 3 12B It"
    write_gguf(
        bundle / "model-Q6_K.gguf",
        model_metadata("gemma3", name, 3840),
    )
    for suffix in ("f16", "Q8_0"):
        metadata = projector_metadata("gemma3", name, 3840)
        metadata.pop("general.file_type")
        path = write_gguf(bundle / f"mmproj-{suffix}.gguf", metadata)
        if suffix == "Q8_0":
            preferred = path

    result = resolve_direct_projector(
        "bundle/model-Q6_K.gguf",
        AUTO_PROJECTOR,
        catalog=ModelCatalog([tmp_path]),
    )

    assert result.projector_path == preferred


def test_known_filename_and_metadata_quantization_disagreement_fails_closed(tmp_path):
    bundle = tmp_path / "bundle"
    name = "Gemma 3 12B It"
    write_gguf(
        bundle / "model-Q6_K.gguf",
        model_metadata("gemma3", name, 3840),
    )
    metadata = projector_metadata("gemma3", name, 3840)
    metadata["general.file_type"] = 1
    write_gguf(bundle / "mmproj-Q8_0.gguf", metadata)

    with pytest.raises(ProjectorResolutionError, match="inconclusive"):
        resolve_direct_projector(
            "bundle/model-Q6_K.gguf",
            AUTO_PROJECTOR,
            catalog=ModelCatalog([tmp_path]),
        )


def test_distinct_compatible_identities_are_ambiguous(tmp_path):
    models = tmp_path / "models"
    projectors = tmp_path / "projectors"
    repo = "https://huggingface.co/google/gemma-3-12b-it"
    write_gguf(
        models / "model.gguf",
        model_metadata(
            "gemma3",
            "Local derivative",
            3840,
            tags=["image-text-to-text"],
            base_repo=repo,
        ),
    )
    for index, name in enumerate(("Gemma Variant A", "Gemma Variant B")):
        write_gguf(
            projectors / f"mmproj-variant-{index}-f16.gguf",
            projector_metadata(
                "gemma3",
                name,
                3840,
                base_repo=repo,
            ),
        )

    with pytest.raises(ProjectorResolutionError, match="ambiguous"):
        resolve_direct_projector(
            "model.gguf",
            AUTO_PROJECTOR,
            catalog=ModelCatalog([models, projectors]),
        )


def test_same_name_but_distinct_projector_graph_types_are_ambiguous(tmp_path):
    bundle = tmp_path / "gemma4"
    name = "Gemma 4 26B A4B It"
    write_gguf(
        bundle / "model-Q6_K.gguf",
        model_metadata("gemma4", name, 2816, tags=["image-text-to-text"]),
    )
    for projector_type in ("gemma4v", "gemma4uv"):
        write_gguf(
            bundle / f"mmproj-{projector_type}-f16.gguf",
            projector_metadata(projector_type, name, 2816),
        )

    with pytest.raises(ProjectorResolutionError, match="ambiguous"):
        resolve_direct_projector(
            "gemma4/model-Q6_K.gguf",
            AUTO_PROJECTOR,
            catalog=ModelCatalog([tmp_path]),
        )


def test_guarded_legacy_minicpm_uses_resampler_descriptor(tmp_path):
    bundle = tmp_path / "minicpm-v-4_5-bakeoff"
    write_gguf(
        bundle / "MiniCPM-V-4_5-Q6_K.gguf",
        model_metadata("qwen3", "Model", 4096),
    )
    projector = write_gguf(
        bundle / "mmproj-model-f16.gguf",
        {
            "general.architecture": "clip",
            "general.description": "image encoder for MiniCPM-V",
            "general.file_type": 1,
            "clip.has_vision_encoder": True,
            "clip.has_minicpmv_projector": True,
            "clip.minicpmv_version": 6,
            "clip.projector_type": "resampler",
            "clip.vision.projection_dim": 0,
        },
        [("resampler.proj.weight", (4096, 4096))],
    )

    result = resolve_direct_projector(
        "minicpm-v-4_5-bakeoff/MiniCPM-V-4_5-Q6_K.gguf",
        AUTO_PROJECTOR,
        catalog=ModelCatalog([tmp_path]),
    )

    assert result.projector_path == projector
    assert "family=minicpm-v-legacy" in result.evidence


@pytest.mark.parametrize(
    ("description", "vision", "projector_type", "dimensions"),
    [
        ("unidentified image encoder", True, "resampler", (4096, 4096)),
        ("image encoder for MiniCPM-V", False, "resampler", (4096, 4096)),
        ("image encoder for MiniCPM-V", True, "mlp", (4096, 4096)),
        ("image encoder for MiniCPM-V", True, "resampler", (2048, 4096)),
    ],
)
def test_legacy_minicpm_missing_guard_never_auto_pairs(
    tmp_path, description, vision, projector_type, dimensions
):
    bundle = tmp_path / "minicpm-v-4_5-bakeoff"
    write_gguf(
        bundle / "MiniCPM-V-4_5-Q6_K.gguf",
        model_metadata("qwen3", "Model", 4096),
    )
    write_gguf(
        bundle / "mmproj-model-f16.gguf",
        {
            "general.architecture": "clip",
            "general.description": description,
            "clip.has_vision_encoder": vision,
            "clip.has_minicpmv_projector": True,
            "clip.minicpmv_version": 6,
            "clip.projector_type": projector_type,
            "clip.vision.projection_dim": 0,
        },
        [("resampler.proj.weight", dimensions)],
    )

    with pytest.raises(ProjectorResolutionError):
        resolve_direct_projector(
            "minicpm-v-4_5-bakeoff/MiniCPM-V-4_5-Q6_K.gguf",
            AUTO_PROJECTOR,
            catalog=ModelCatalog([tmp_path]),
        )


@pytest.mark.parametrize(
    ("capability", "version"),
    [
        (False, 6),
        (True, 0),
        (True, 5),
        (True, 7),
    ],
)
def test_legacy_minicpm_rejects_missing_flag_and_unsupported_versions(
    tmp_path, capability, version
):
    bundle = tmp_path / "minicpm-v-4_5-bakeoff"
    write_gguf(
        bundle / "MiniCPM-V-4_5-Q6_K.gguf",
        model_metadata("qwen3", "Model", 4096),
    )
    write_gguf(
        bundle / "mmproj-model-f16.gguf",
        {
            "general.architecture": "clip",
            "general.description": "image encoder for MiniCPM-V",
            "clip.has_vision_encoder": True,
            "clip.has_minicpmv_projector": capability,
            "clip.minicpmv_version": version,
            "clip.projector_type": "resampler",
            "clip.vision.projection_dim": 0,
        },
        [("resampler.proj.weight", (4096, 4096))],
    )

    with pytest.raises(ProjectorResolutionError, match="inconclusive"):
        resolve_direct_projector(
            "minicpm-v-4_5-bakeoff/MiniCPM-V-4_5-Q6_K.gguf",
            AUTO_PROJECTOR,
            catalog=ModelCatalog([tmp_path]),
        )


def test_known_vlm_without_projector_fails_but_text_or_unknown_is_text_only(tmp_path):
    write_gguf(
        tmp_path / "vision.gguf",
        model_metadata(
            "gemma3",
            "Gemma 3 Vision",
            3840,
            tags=["image-text-to-text"],
        ),
    )
    write_gguf(
        tmp_path / "text.gguf",
        model_metadata("llama", "Text Model", 4096),
    )
    (tmp_path / "unknown.gguf").write_bytes(b"not inspectable")
    catalog = ModelCatalog([tmp_path])

    with pytest.raises(ProjectorResolutionError, match="known vision model"):
        resolve_direct_projector("vision.gguf", AUTO_PROJECTOR, catalog=catalog)
    text = resolve_direct_projector("text.gguf", AUTO_PROJECTOR, catalog=catalog)
    unknown = resolve_direct_projector("unknown.gguf", AUTO_PROJECTOR, catalog=catalog)

    assert text.outcome == "auto_text_only"
    assert unknown.outcome == "auto_text_only"


def test_flat_root_unrelated_projector_does_not_block_parseable_text_model(tmp_path):
    write_gguf(
        tmp_path / "text-model.gguf",
        model_metadata("llama", "Text Model", 4096),
    )
    write_gguf(
        tmp_path / "mmproj-unrelated-f16.gguf",
        projector_metadata("gemma3", "Unrelated Gemma Projector", 3840),
    )

    result = resolve_direct_projector(
        "text-model.gguf",
        AUTO_PROJECTOR,
        catalog=ModelCatalog([tmp_path]),
    )

    assert result.outcome == "auto_text_only"
    assert result.projector_path is None


def test_unknown_model_with_same_identity_adjacent_projector_requires_a_choice(tmp_path):
    write_gguf(
        tmp_path / "Future-VLM-Q6_K.gguf",
        model_metadata("futurevlm", "Future VLM", 3072),
    )
    write_gguf(
        tmp_path / "Future-VLM-mmproj-f16.gguf",
        {
            "general.architecture": "clip",
            "general.type": "mmproj",
            "general.name": "Future VLM",
            "general.file_type": 1,
            "clip.has_vision_encoder": True,
            "clip.projector_type": "future_merger",
            "clip.vision.projection_dim": 3072,
        },
    )

    with pytest.raises(ProjectorResolutionError, match="cannot classify the model family"):
        resolve_direct_projector(
            "Future-VLM-Q6_K.gguf",
            AUTO_PROJECTOR,
            catalog=ModelCatalog([tmp_path]),
        )


def test_unknown_model_with_global_strong_identity_requires_a_choice(tmp_path):
    models = tmp_path / "models"
    projectors = tmp_path / "projectors"
    write_gguf(
        models / "Future-VLM-Q6_K.gguf",
        model_metadata("futurevlm", "Future VLM", 3072),
    )
    write_gguf(
        projectors / "mmproj-unrelated-name-f16.gguf",
        {
            "general.architecture": "clip",
            "general.type": "mmproj",
            "general.name": "Future VLM",
            "general.file_type": 1,
            "clip.has_vision_encoder": True,
            "clip.projector_type": "future_merger",
            "clip.vision.projection_dim": 3072,
        },
    )

    with pytest.raises(ProjectorResolutionError, match="cannot classify the model family"):
        resolve_direct_projector(
            "Future-VLM-Q6_K.gguf",
            AUTO_PROJECTOR,
            catalog=ModelCatalog([models, projectors]),
        )


def test_resolver_enforces_one_aggregate_header_scan_budget(tmp_path, monkeypatch):
    write_gguf(
        tmp_path / "vision.gguf",
        model_metadata(
            "gemma3",
            "Gemma 3 Vision",
            3840,
            tags=["image-text-to-text"],
        ),
    )
    monkeypatch.setattr(projector_module, "MAX_RESOLVER_SCAN_BYTES", 64)

    with pytest.raises(ProjectorResolutionError, match="aggregate GGUF scan budget"):
        resolve_direct_projector(
            "vision.gguf",
            AUTO_PROJECTOR,
            catalog=ModelCatalog([tmp_path]),
        )


def test_resolver_scan_budget_also_counts_failed_candidate_headers(tmp_path, monkeypatch):
    models = tmp_path / "models"
    projectors = tmp_path / "projectors"
    model = write_gguf(
        models / "vision.gguf",
        model_metadata(
            "gemma3",
            "Gemma 3 Vision",
            3840,
            tags=["image-text-to-text"],
        ),
    )
    model_scan = gguf_metadata.read_gguf_metadata(model).scanned_bytes
    projectors.mkdir()
    for suffix in ("a", "b", "c"):
        (projectors / f"mmproj-{suffix}.gguf").write_bytes(b"NOPE")
    monkeypatch.setattr(projector_module, "MAX_RESOLVER_SCAN_BYTES", model_scan + 8)

    with pytest.raises(ProjectorResolutionError, match="aggregate GGUF scan budget"):
        resolve_direct_projector(
            "vision.gguf",
            AUTO_PROJECTOR,
            catalog=ModelCatalog([models, projectors]),
        )


def test_error_candidate_names_are_bounded_and_catalog_relative(tmp_path):
    bundle = tmp_path / "bundle"
    write_gguf(
        bundle / "target.gguf",
        model_metadata("gemma3", "Target Vision Model", 3840),
    )
    for index in range(10):
        write_gguf(
            bundle / f"mmproj-unresolved-{index}-f16.gguf",
            projector_metadata("gemma3", f"Other Projector {index}", 3840),
        )

    with pytest.raises(ProjectorResolutionError) as captured:
        resolve_direct_projector(
            "bundle/target.gguf",
            AUTO_PROJECTOR,
            catalog=ModelCatalog([tmp_path]),
        )

    message = str(captured.value)
    assert str(tmp_path) not in message
    assert "and 4 more" in message
    assert len(message) < 1_000


def test_catalog_escape_is_never_resolved_or_exposed(tmp_path):
    outside = tmp_path.parent / "outside-model.gguf"
    outside.write_bytes(b"outside")
    link = tmp_path / "escaped.gguf"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are unavailable")

    with pytest.raises(ProjectorResolutionError) as captured:
        resolve_direct_projector(
            "escaped.gguf",
            NONE_PROJECTOR,
            catalog=ModelCatalog([tmp_path]),
        )

    assert str(outside) not in str(captured.value)
