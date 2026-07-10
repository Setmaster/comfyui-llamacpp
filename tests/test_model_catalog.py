from __future__ import annotations

import os

import pytest

from models.catalog import ModelCatalog, ModelCatalogError
from models.identity import RouterIdentityError, resolve_router_model


def test_catalog_discovers_models_and_mmproj_recursively(tmp_path):
    (tmp_path / "bundle").mkdir()
    (tmp_path / "text.gguf").write_bytes(b"x")
    (tmp_path / "bundle" / "vision.gguf").write_bytes(b"x")
    (tmp_path / "bundle" / "mmproj-model.gguf").write_bytes(b"x")
    catalog = ModelCatalog([tmp_path])
    assert catalog.list_models() == ["bundle/vision.gguf", "text.gguf"]
    assert catalog.list_mmproj() == ["bundle/mmproj-model.gguf"]


@pytest.mark.parametrize("name", ["../escape.gguf", "/tmp/escape.gguf", "a/../../b.gguf"])
def test_catalog_rejects_traversal(tmp_path, name):
    catalog = ModelCatalog([tmp_path])
    with pytest.raises(ModelCatalogError):
        catalog.resolve(name, require_file=False)


def test_catalog_rejects_symlink_escape(tmp_path):
    outside = tmp_path.parent / "outside.gguf"
    outside.write_bytes(b"x")
    link = tmp_path / "linked.gguf"
    try:
        os.symlink(outside, link)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(ModelCatalogError):
        ModelCatalog([tmp_path]).resolve("linked.gguf")


def test_router_identity_resolves_exact_id_alias_and_bundle_name():
    records = [
        {"id": "qwen", "aliases": ["Qwen-Alias"]},
        {"id": "vision-bundle"},
    ]
    assert resolve_router_model("qwen.gguf", records) == "qwen"
    assert resolve_router_model("qwen-alias", records) == "qwen"
    assert resolve_router_model("vision-bundle/model.gguf", records) == "vision-bundle"


def test_router_identity_fails_on_ambiguity_and_missing():
    with pytest.raises(RouterIdentityError, match="ambiguous"):
        resolve_router_model("bundle/model.gguf", [{"id": "bundle"}, {"id": "model"}])
    with pytest.raises(RouterIdentityError, match="not found"):
        resolve_router_model("missing.gguf", [{"id": "present"}])
