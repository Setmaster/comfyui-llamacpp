from __future__ import annotations

import pytest

from models.catalog import ModelCatalog
from runtime.client import LlamaClientError, ModelState, RouterModel, ServerProps
from runtime.discovery import (
    DiscoverySource,
    Fact,
    KnowledgeState,
    RuntimeEndpointSnapshot,
    RuntimeOwnership,
    SavedModelState,
    discover_runtime,
)
from runtime.service import RuntimeMode


class FakeClient:
    def __init__(self, *, models=(), props=None, props_error=None):
        self.model_records = tuple(models)
        self.props_record = props
        self.props_error = props_error
        self.model_calls = []
        self.props_calls = []

    def models(self, **kwargs):
        self.model_calls.append(dict(kwargs))
        return self.model_records

    def passive_props(self, model):
        self.props_calls.append(model)
        if self.props_error is not None:
            raise self.props_error
        assert self.props_record is not None
        return self.props_record


def endpoint(
    mode=RuntimeMode.ROUTER,
    *,
    owned=True,
    epoch=7,
    context=8192,
):
    return RuntimeEndpointSnapshot(
        mode=mode,
        owned=owned,
        runtime_epoch=epoch,
        endpoint="http://127.0.0.1:8080",
        active_router_root="/models/router" if mode == RuntimeMode.ROUTER else None,
        configured_context=context,
        configured_model_path="/models/direct.gguf" if mode == RuntimeMode.DIRECT else None,
    )


def props(*, modalities=None, model="vision.gguf", context=4096, sleeping=False):
    return ServerProps(
        role=None,
        build_info="b9957",
        model_path=f"/models/{model}",
        model_alias=model,
        is_sleeping=sleeping,
        modalities=modalities or {},
        raw={
            "model_path": f"/models/{model}",
            "model_alias": model,
            "modalities": modalities or {},
            "default_generation_settings": {"n_ctx": context},
        },
    )


def router_model(
    model_id,
    state=ModelState.UNLOADED,
    *,
    aliases=(),
    modalities=("text",),
):
    return RouterModel(
        model_id,
        state,
        aliases=aliases,
        input_modalities=modalities,
    )


def test_router_discovery_is_passive_and_live_catalog_is_authoritative(tmp_path):
    (tmp_path / "offline-only.gguf").write_bytes(b"offline")
    client = FakeClient(
        models=(
            router_model("text", aliases=("text.gguf",)),
            router_model(
                "vision",
                ModelState.LOADED,
                aliases=("vision.gguf",),
                modalities=("text", "image"),
            ),
        ),
        props=props(modalities={"vision": True, "audio": False, "video": False}),
    )

    result = discover_runtime(
        endpoint(),
        client,
        saved_model="vision.gguf",
        catalog=ModelCatalog([tmp_path]),
        clock=lambda: 123.0,
    )

    assert client.model_calls == [{"reload": False}]
    assert client.props_calls == ["vision"]
    assert [model.model_id for model in result.models] == ["text", "vision"]
    assert "offline-only.gguf" not in {model.model_id for model in result.models}
    assert result.saved_model == "vision.gguf"
    assert result.saved_model_state == SavedModelState.AVAILABLE
    assert result.active_router_root == "/models/router"
    assert result.captured_at == 123.0

    vision = result.models[1]
    assert vision.ownership == RuntimeOwnership.OWNED
    assert vision.release_scope == "router_model"
    assert vision.context_length.value == 4096
    assert vision.context_length.source == DiscoverySource.PROPS
    assert vision.input_capabilities["image"].value is True
    assert vision.input_capabilities["audio"].value is False
    assert vision.input_capabilities["video"].value is False


def test_router_list_presence_is_known_but_absence_remains_unknown():
    client = FakeClient(
        models=(
            router_model(
                "vision",
                ModelState.UNLOADED,
                modalities=("text", "image"),
            ),
        ),
        props_error=LlamaClientError("model is not loaded", status_code=400),
    )

    result = discover_runtime(endpoint(), client, saved_model="vision")

    descriptor = result.models[0]
    assert descriptor.input_capabilities["text"].state == KnowledgeState.KNOWN
    assert descriptor.input_capabilities["image"].state == KnowledgeState.KNOWN
    assert descriptor.input_capabilities["audio"].state == KnowledgeState.UNKNOWN
    assert descriptor.input_capabilities["video"].state == KnowledgeState.UNKNOWN
    assert descriptor.context_length.state == KnowledgeState.UNKNOWN
    assert result.warnings == ("Selected model is not loaded; passive properties remain Unknown.",)


def test_missing_saved_router_model_is_preserved_without_replacement():
    client = FakeClient(models=(router_model("available"),))

    result = discover_runtime(endpoint(), client, saved_model="missing/model.gguf")

    assert result.saved_model == "missing/model.gguf"
    assert result.saved_model_state == SavedModelState.MISSING
    assert [model.model_id for model in result.models] == ["available"]
    assert client.props_calls == []


def test_passive_auth_or_transport_failure_is_not_disguised_as_unknown():
    for error in (
        LlamaClientError("unauthorized", status_code=401),
        LlamaClientError("transport failed"),
    ):
        client = FakeClient(
            models=(router_model("selected", ModelState.LOADED),),
            props_error=error,
        )
        with pytest.raises(LlamaClientError) as raised:
            discover_runtime(endpoint(), client, saved_model="selected")
        assert raised.value is error


def test_direct_props_explicit_false_is_known_unsupported():
    client = FakeClient(
        props=props(
            modalities={"vision": False, "audio": False, "video": True},
            sleeping=True,
        )
    )

    result = discover_runtime(
        endpoint(RuntimeMode.DIRECT, context=16384),
        client,
        saved_model="vision.gguf",
    )

    assert client.props_calls == [None]
    assert result.saved_model_state == SavedModelState.AVAILABLE
    descriptor = result.models[0]
    assert descriptor.residency == ModelState.SLEEPING
    assert descriptor.release_scope == "direct_runtime"
    assert descriptor.input_capabilities["text"].value is True
    assert descriptor.input_capabilities["image"].value is False
    assert descriptor.input_capabilities["audio"].value is False
    assert descriptor.input_capabilities["video"].value is True


def test_direct_context_falls_back_to_its_exact_launch_configuration():
    unknown_context = props(context=0)
    unknown_context.raw["default_generation_settings"] = {}

    result = discover_runtime(
        endpoint(RuntimeMode.DIRECT, context=16384),
        FakeClient(props=unknown_context),
    )

    assert result.models[0].context_length == Fact.known(
        16384,
        DiscoverySource.LAUNCH_CONFIG,
    )


def test_direct_discovery_uses_proven_configuration_or_canonical_running_sentinel():
    unknown_identity = ServerProps(
        role=None,
        build_info="b9957",
        model_path=None,
        model_alias=None,
        is_sleeping=False,
        modalities={},
        raw={},
    )

    configured = discover_runtime(
        endpoint(RuntimeMode.DIRECT),
        FakeClient(props=unknown_identity),
        saved_model="direct.gguf",
    )
    assert [model.model_id for model in configured.models] == ["direct.gguf"]
    assert configured.saved_model_state == SavedModelState.AVAILABLE

    unproven = discover_runtime(
        RuntimeEndpointSnapshot(
            mode=RuntimeMode.DIRECT,
            owned=True,
            runtime_epoch=7,
            endpoint="http://127.0.0.1:8080",
        ),
        FakeClient(props=unknown_identity),
        saved_model="unproven.gguf",
    )
    assert [model.model_id for model in unproven.models] == ["(use running model)"]
    assert unproven.saved_model == "unproven.gguf"
    assert unproven.saved_model_state == SavedModelState.MISSING


def test_attached_props_never_claim_owned_release():
    client = FakeClient(props=props())

    result = discover_runtime(
        endpoint(RuntimeMode.ATTACHED, owned=False, epoch=None),
        client,
    )

    descriptor = result.models[0]
    assert descriptor.ownership == RuntimeOwnership.ATTACHED
    assert descriptor.release_scope == "attached"
    assert result.owned is False


def test_offline_catalog_is_labelled_and_projector_is_suggestion_only(tmp_path):
    bundle = tmp_path / "vision"
    bundle.mkdir()
    (bundle / "model.gguf").write_bytes(b"model")
    (bundle / "mmproj-model.gguf").write_bytes(b"projector")

    result = discover_runtime(
        RuntimeEndpointSnapshot(
            mode=RuntimeMode.NONE,
            owned=False,
            runtime_epoch=None,
            endpoint="",
        ),
        None,
        saved_model="vision/model.gguf",
        catalog=ModelCatalog([tmp_path]),
        clock=lambda: 456.0,
    )

    assert result.saved_model_state == SavedModelState.AVAILABLE
    assert result.warnings == (
        "Offline local catalog; entries are not proof of runtime callability.",
    )
    descriptor = result.models[0]
    assert descriptor.source == DiscoverySource.LOCAL_CATALOG
    assert descriptor.ownership == RuntimeOwnership.OFFLINE
    assert descriptor.residency == ModelState.UNKNOWN
    assert descriptor.projector is not None
    assert descriptor.projector.projector_name == "vision/mmproj-model.gguf"
    assert descriptor.projector.compatibility.state == KnowledgeState.UNKNOWN
    assert descriptor.projector.requires_confirmation is True
    assert descriptor.projector.evidence == "unique_adjacent_file"


def test_offline_discovery_scans_the_catalog_once_for_all_projector_suggestions(tmp_path):
    for index in range(4):
        bundle = tmp_path / f"bundle-{index}"
        bundle.mkdir()
        (bundle / "model.gguf").write_bytes(b"model")
        (bundle / "mmproj-model.gguf").write_bytes(b"projector")
    catalog = ModelCatalog([tmp_path])
    original_entries = catalog.entries
    calls = 0

    def counted_entries():
        nonlocal calls
        calls += 1
        return original_entries()

    catalog.entries = counted_entries  # type: ignore[method-assign]
    result = discover_runtime(
        RuntimeEndpointSnapshot(RuntimeMode.NONE, False, None, ""),
        None,
        catalog=catalog,
    )

    assert calls == 1
    assert len(result.models) == 4
    assert all(model.projector is not None for model in result.models)


def test_live_direct_projector_uses_proven_model_path_without_a_saved_hint(tmp_path):
    bundle = tmp_path / "vision"
    bundle.mkdir()
    model_path = bundle / "model.gguf"
    model_path.write_bytes(b"model")
    (bundle / "mmproj-model.gguf").write_bytes(b"projector")
    direct_props = ServerProps(
        role=None,
        build_info="b9957",
        model_path=str(model_path),
        model_alias="friendly-running-model",
        is_sleeping=False,
        modalities={"vision": True},
        raw={"model_path": str(model_path), "modalities": {"vision": True}},
    )
    snapshot = RuntimeEndpointSnapshot(
        mode=RuntimeMode.DIRECT,
        owned=True,
        runtime_epoch=7,
        endpoint="http://127.0.0.1:8080",
        configured_model_path=str(model_path),
    )

    result = discover_runtime(
        snapshot,
        FakeClient(props=direct_props),
        catalog=ModelCatalog([tmp_path]),
    )

    assert result.saved_model_state == SavedModelState.UNSPECIFIED
    assert result.models[0].projector is not None
    assert result.models[0].projector.projector_name == "vision/mmproj-model.gguf"
    assert result.models[0].projector.requires_confirmation is True


def test_live_router_projector_uses_canonical_id_when_saved_value_is_an_alias(tmp_path):
    bundle = tmp_path / "vision"
    bundle.mkdir()
    (bundle / "model.gguf").write_bytes(b"model")
    (bundle / "mmproj-model.gguf").write_bytes(b"projector")
    client = FakeClient(
        models=(
            router_model(
                "vision/model.gguf",
                ModelState.LOADED,
                aliases=("vlm",),
                modalities=("text", "image"),
            ),
        ),
        props=props(modalities={"vision": True}),
    )

    result = discover_runtime(
        endpoint(),
        client,
        saved_model="vlm",
        catalog=ModelCatalog([tmp_path]),
    )

    assert result.models[0].projector is not None
    assert result.models[0].projector.projector_name == "vision/mmproj-model.gguf"

    (bundle / "second-mmproj.gguf").write_bytes(b"projector")
    ambiguous = discover_runtime(
        endpoint(),
        client,
        saved_model="vlm",
        catalog=ModelCatalog([tmp_path]),
    )
    assert ambiguous.models[0].projector is None


def test_unknown_facts_and_endpoint_text_are_strictly_bounded():
    with pytest.raises(ValueError, match="unknown facts"):
        Fact(KnowledgeState.UNKNOWN, None, DiscoverySource.ROUTER)
    with pytest.raises(ValueError, match="endpoint exceeds"):
        RuntimeEndpointSnapshot(RuntimeMode.NONE, False, None, "é" * 3000)


def test_discovery_payload_is_json_safe_and_retains_raw_missing_value():
    client = FakeClient(models=(router_model("available"),))
    result = discover_runtime(endpoint(), client, saved_model="missing.gguf")

    payload = result.as_dict()

    assert payload["schema_version"] == 1
    assert payload["mode"] == "router"
    assert payload["saved_model"] == "missing.gguf"
    assert payload["saved_model_state"] == "missing"
    assert payload["models"][0]["input_capabilities"]["audio"] == {
        "state": "unknown",
        "value": None,
        "source": None,
    }

    public = result.public_dict()
    assert "endpoint" not in public
    assert "active_router_root" not in public
    assert "model_path" not in public["models"][0]
