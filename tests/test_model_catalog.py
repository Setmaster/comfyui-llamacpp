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
        external_gguf.resolve(),
        (default_models / "LLM" / "gguf").resolve(),
    ]


def test_register_model_folder_preserves_existing_and_yaml_priority(tmp_path, monkeypatch):
    default_models = tmp_path / "comfy-models"
    existing = tmp_path / "existing"
    external = tmp_path / "external" / "gguf"
    existing.mkdir()
    external.mkdir(parents=True)

    class FakeFolderPaths:
        models_dir = str(default_models)

        def __init__(self):
            self.paths = {
                catalog_module.FOLDER_KEY: [str(existing)],
                "LLM": [str(external)],
            }

        def get_folder_paths(self, key):
            return list(self.paths[key])

        def add_model_folder_path(self, key, path, is_default=False):
            values = self.paths.setdefault(key, [])
            if path not in values:
                values.insert(0, path) if is_default else values.append(path)

    fake = FakeFolderPaths()
    monkeypatch.setattr(catalog_module, "_folder_paths_module", lambda: fake)

    catalog_module.register_model_folder()
    catalog_module.register_model_folder()

    assert fake.paths[catalog_module.FOLDER_KEY] == [
        str(existing),
        str(external),
        str((default_models / "LLM" / "gguf").resolve()),
    ]


def test_llm_path_without_gguf_child_remains_supported(tmp_path):
    legacy_root = tmp_path / "legacy-llm"
    legacy_root.mkdir()
    assert catalog_module._gguf_root_from_llm_path(legacy_root) == legacy_root.resolve()


def test_router_root_auto_selects_the_most_populated_upstream_visible_root(tmp_path):
    empty_default = tmp_path / "default"
    external = tmp_path / "external"
    empty_default.mkdir()
    (external / "bundle-a").mkdir(parents=True)
    (external / "bundle-b").mkdir()
    (external / "bundle-a" / "model.gguf").write_bytes(b"x")
    (external / "bundle-a" / "mmproj-model.gguf").write_bytes(b"x")
    (external / "bundle-b" / "model.gguf").write_bytes(b"x")

    catalog = ModelCatalog([empty_default, external])

    assert catalog.preferred_router_root() == external.resolve()
    assert catalog.resolve_router_root("(auto)") == external.resolve()


def test_router_root_scoring_matches_upstream_one_level_scan(tmp_path):
    deep_only = tmp_path / "deep"
    visible = tmp_path / "visible"
    (deep_only / "one" / "two").mkdir(parents=True)
    visible.mkdir()
    (deep_only / "one" / "two" / "hidden.gguf").write_bytes(b"x")
    (visible / "visible.gguf").write_bytes(b"x")

    catalog = ModelCatalog([deep_only, visible])

    assert catalog.preferred_router_root() == visible.resolve()


def test_router_root_scoring_matches_upstream_case_sensitive_suffix(tmp_path):
    ignored = tmp_path / "ignored"
    visible = tmp_path / "visible"
    ignored.mkdir()
    visible.mkdir()
    (ignored / "uppercase.GGUF").write_bytes(b"x")
    (visible / "lowercase.gguf").write_bytes(b"x")

    assert ModelCatalog([ignored, visible]).preferred_router_root() == visible.resolve()


def test_router_root_scoring_accepts_complete_shards_and_rejects_ambiguous_bundles(tmp_path):
    safe = tmp_path / "safe"
    ambiguous = tmp_path / "ambiguous"
    (safe / "sharded").mkdir(parents=True)
    (ambiguous / "bundle-a").mkdir(parents=True)
    (ambiguous / "bundle-b").mkdir()
    for index in (1, 2):
        (safe / "sharded" / f"model-{index:05d}-of-00002.gguf").write_bytes(b"x")
    (ambiguous / "bundle-a" / "q5.gguf").write_bytes(b"x")
    (ambiguous / "bundle-a" / "q6.gguf").write_bytes(b"x")
    (ambiguous / "bundle-b" / "model.gguf").write_bytes(b"x")
    (ambiguous / "bundle-b" / "mmproj-a.gguf").write_bytes(b"x")
    (ambiguous / "bundle-b" / "mmproj-b.gguf").write_bytes(b"x")

    assert ModelCatalog._router_preset_count(safe) == 1
    assert ModelCatalog._router_preset_count(ambiguous) == 0


def test_router_root_scoring_classifies_projectors_case_insensitively(tmp_path):
    projector_only = tmp_path / "projector-only"
    bundle_root = tmp_path / "bundle-root"
    projector_only.mkdir()
    (bundle_root / "vision").mkdir(parents=True)
    (projector_only / "MMPROJ-model.gguf").write_bytes(b"x")
    (bundle_root / "vision" / "model.gguf").write_bytes(b"x")
    (bundle_root / "vision" / "MMPROJ-model.gguf").write_bytes(b"x")

    assert ModelCatalog._router_preset_count(projector_only) == 0
    assert ModelCatalog._router_preset_count(bundle_root) == 1


def test_router_root_scoring_rejects_symlink_escape(tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "model.gguf").write_bytes(b"x")
    try:
        os.symlink(outside, root / "escaped")
    except OSError:
        pytest.skip("symlinks unavailable")

    assert ModelCatalog._router_preset_count(root) == 0


def test_router_root_auto_uses_first_existing_root_when_all_are_empty(tmp_path):
    missing = tmp_path / "missing"
    existing = tmp_path / "existing"
    existing.mkdir()

    assert ModelCatalog([missing, existing]).preferred_router_root() == existing.resolve()


def test_explicit_router_root_must_be_configured_and_exist(tmp_path):
    configured = tmp_path / "configured"
    configured.mkdir()
    catalog = ModelCatalog([configured])

    assert catalog.resolve_router_root(str(configured)) == configured.resolve()
    with pytest.raises(ModelCatalogError, match="must be one of"):
        catalog.resolve_router_root(str(tmp_path / "other"))
    configured.rmdir()
    with pytest.raises(ModelCatalogError, match="does not exist"):
        catalog.resolve_router_root(str(configured))


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


def test_adjacent_projector_is_suggestion_only_when_unique(tmp_path):
    bundle = tmp_path / "vision"
    bundle.mkdir()
    model = bundle / "model.gguf"
    projector = bundle / "mmproj-model.gguf"
    model.write_bytes(b"model")
    projector.write_bytes(b"projector")
    catalog = ModelCatalog([tmp_path])

    suggestion = catalog.adjacent_projector("vision/model.gguf")

    assert suggestion is not None
    assert suggestion.name == "vision/mmproj-model.gguf"
    assert suggestion.path == projector.resolve()
    assert suggestion.is_mmproj is True


def test_adjacent_projector_refuses_ambiguous_or_other_directory_candidates(tmp_path):
    bundle = tmp_path / "vision"
    bundle.mkdir()
    (bundle / "model.gguf").write_bytes(b"model")
    (bundle / "mmproj-a.gguf").write_bytes(b"a")
    (bundle / "mmproj-b.gguf").write_bytes(b"b")
    other = tmp_path / "other"
    other.mkdir()
    (other / "mmproj-only.gguf").write_bytes(b"other")
    catalog = ModelCatalog([tmp_path])

    assert catalog.adjacent_projector("vision/model.gguf") is None

    (bundle / "mmproj-b.gguf").unlink()
    (bundle / "mmproj-a.gguf").unlink()
    assert catalog.adjacent_projector("vision/model.gguf") is None
