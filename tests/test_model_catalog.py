from __future__ import annotations

import os

import pytest

import models.catalog as catalog_module
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


def test_configured_roots_include_existing_comfy_llm_gguf_folder(tmp_path, monkeypatch):
    default_models = tmp_path / "comfy-models"
    external_llm = tmp_path / "external" / "LLM"
    external_gguf = external_llm / "gguf"
    external_gguf.mkdir(parents=True)

    class FakeFolderPaths:
        models_dir = str(default_models)
        base_path = str(tmp_path / "comfy")

        def __init__(self):
            self.paths = {"LLM": [str(external_llm)]}

        def get_folder_paths(self, key):
            return list(self.paths[key])

        def add_model_folder_path(self, key, path, is_default=False):
            values = self.paths.setdefault(key, [])
            if path in values:
                values.remove(path)
            values.insert(0, path) if is_default else values.append(path)

    fake = FakeFolderPaths()
    monkeypatch.setattr(catalog_module, "_folder_paths_module", lambda: fake)

    roots = catalog_module._configured_roots()

    assert roots == [
        (default_models / "LLM" / "gguf").resolve(),
        external_gguf.resolve(),
    ]


def test_llm_path_without_gguf_child_remains_supported(tmp_path):
    legacy_root = tmp_path / "legacy-llm"
    legacy_root.mkdir()
    assert catalog_module._gguf_root_from_llm_path(legacy_root) == legacy_root.resolve()


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
