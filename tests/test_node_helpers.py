from __future__ import annotations

import importlib
import json
import sys
from contextlib import contextmanager

import numpy as np
import pytest


def test_token_ban_supports_json_entries_containing_commas(node_package):
    node = node_package.NODE_CLASS_MAPPINGS["LlamaCppTokenBan"]()
    value = node.create_ban_list('["one,two", " three "]', True)[0]
    assert value == [["one,two", False], [" three ", False]]


def test_canonical_image_batch_is_preflight_bounded_before_any_encoding(
    node_package,
    monkeypatch,
):
    common = importlib.import_module(f"{node_package.__name__}.nodes.common")
    encoded = []
    monkeypatch.setattr(
        common,
        "image_tensor_to_data_urls_bounded",
        lambda *args, **kwargs: encoded.append((args, kwargs)) or [],
    )
    batch = np.zeros((4097, 1, 1, 3), dtype=np.float32)

    with pytest.raises(ValueError, match="exceeds 4096 frames"):
        common.collect_images(
            1,
            {"image_1": batch},
            include_batch=True,
            maximum_images=4096,
        )

    assert encoded == []


def test_canonical_image_conversion_error_is_generic_and_bounded(
    node_package,
    monkeypatch,
):
    module = importlib.import_module(f"{node_package.__name__}.nodes.generate")
    monkeypatch.setattr(
        module,
        "collect_images",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("S" * 10_000)),
    )

    with pytest.raises(module.CanonicalGenerationError) as caught:
        module.LlamaCppGenerate().generate("hello", image_amount=1, image_1=object())

    assert caught.value.category == module.ErrorCategory.INVALID_REQUEST
    assert len(caught.value.info.message) <= 4096
    assert "S" * 100 not in caught.value.info.message


def test_structured_output_builds_nested_json_schema(node_package):
    node = node_package.NODE_CLASS_MAPPINGS["LlamaCppStructuredOutput"]()
    value = node.create_constraint(
        "json_schema",
        '{"type":"object"}',
        True,
        "answer",
        True,
    )[0]
    assert value["json_schema"] == {
        "name": "answer",
        "strict": True,
        "schema": {"type": "object"},
    }


def test_structured_output_rejects_invalid_schema(node_package):
    node = node_package.NODE_CLASS_MAPPINGS["LlamaCppStructuredOutput"]()
    with pytest.raises(ValueError, match="Invalid JSON schema"):
        node.create_constraint("json_schema", "{", True)


@pytest.mark.parametrize(
    ("managed", "expected_model", "expected_resolutions"),
    [
        (False, "attached-model", []),
        (True, "owned-router-model", ["selected-model"]),
    ],
)
def test_prompt_model_resolution_is_scoped_to_the_managed_router(
    node_package,
    monkeypatch,
    managed,
    expected_model,
    expected_resolutions,
):
    common = importlib.import_module(f"{node_package.__name__}.nodes.common")
    observed = {"leases": [], "payloads": [], "resolutions": []}

    class FakeManager:
        is_router_mode = True
        is_running = True

        def connection_for(self, server_url, **kwargs):
            del server_url, kwargs
            return object(), managed

        @contextmanager
        def generation_lease(self, *, managed):
            observed["leases"].append(managed)
            yield

        def resolve_model_id(self, model):
            observed["resolutions"].append(model)
            return "owned-router-model"

    def fake_stream_chat(connection, payload, **kwargs):
        del connection, kwargs
        observed["payloads"].append(payload)
        return common.StreamResult("ok", "", True)

    monkeypatch.setattr(common, "stream_chat", fake_stream_chat)

    result = common.run_prompt(
        "hello",
        model="attached-model" if not managed else "selected-model",
        server_url="http://127.0.0.1:9999" if not managed else "",
        manager=FakeManager(),
    )

    assert result.success is True
    assert observed["leases"] == [managed]
    assert observed["resolutions"] == expected_resolutions
    assert observed["payloads"][0]["model"] == expected_model


def test_plaintext_conversion_handles_images_links_html_and_ignored_content(node_package):
    node = node_package.NODE_CLASS_MAPPINGS["LlamaCppPromptOutput"]()
    text = "# Title\n![alt](image.png) [link](https://example.com)<br><b>bold</b><script>x</script>"
    output = node.preview_text(text, True)["result"][0]
    assert output == "Title\nalt link\nbold"


def test_list_models_can_request_router_catalog_reload(node_package, monkeypatch):
    node_class = node_package.NODE_CLASS_MAPPINGS["LlamaCppListModels"]
    node = node_class()
    observed = []

    class FakeManager:
        def list_models(self, *, reload=False):
            observed.append(reload)
            return (
                True,
                [{"id": "fresh-model", "status": {"value": "unloaded"}}],
                None,
            )

    module = sys.modules[node_class.__module__]
    monkeypatch.setattr(module, "get_server_manager", lambda: FakeManager())

    models_json, models_list = node.list_models(reload_catalog=True)

    assert observed == [True]
    assert '"id": "fresh-model"' in models_json
    assert models_list == "fresh-model (unloaded)"


def test_router_node_appends_and_forwards_configured_model_root(
    node_package, monkeypatch, tmp_path
):
    node_class = node_package.NODE_CLASS_MAPPINGS["StartLlamaCppRouter"]
    node = node_class()
    module = sys.modules[node_class.__module__]
    selections = []
    configs = []

    class FakeManager:
        server_url = "http://127.0.0.1:8080"

        def start_router(self, config, **kwargs):
            configs.append((config, kwargs))
            return True, None

    def resolve(selection):
        selections.append(selection)
        return str(tmp_path)

    monkeypatch.setattr(module, "get_router_models_directory", resolve)
    monkeypatch.setattr(module, "get_server_manager", lambda: FakeManager())

    result = node.start_router(2048, "all", 0, 1, models_directory="chosen-root")

    schema = node_class.INPUT_TYPES()
    assert list(schema["optional"])[-1] == "models_directory"
    assert schema["optional"]["models_directory"][1]["default"] == "(auto)"
    assert selections == ["chosen-root"]
    assert configs[0][0].models_dir == str(tmp_path)
    assert result == ("http://127.0.0.1:8080", True)


def test_router_node_omitted_model_root_retains_auto_default(node_package, monkeypatch, tmp_path):
    node_class = node_package.NODE_CLASS_MAPPINGS["StartLlamaCppRouter"]
    node = node_class()
    module = sys.modules[node_class.__module__]
    selections = []

    class FakeManager:
        server_url = "http://127.0.0.1:8080"

        def start_router(self, config, **kwargs):
            return True, None

    def resolve(selection):
        selections.append(selection)
        return str(tmp_path)

    monkeypatch.setattr(module, "get_router_models_directory", resolve)
    monkeypatch.setattr(module, "get_server_manager", lambda: FakeManager())

    node.start_router(2048, "", 0, 1)

    assert selections == ["(auto)"]


def _status_data(*, running: bool) -> dict:
    return {
        "status": "running" if running else "stopped",
        "is_running": running,
        "mode": "single_model",
        "server_url": "http://127.0.0.1:8080" if running else None,
        "last_error": None,
        "process": {
            "pid": 1234 if running else None,
            "process_group_id": None,
            "windows_job_assigned": running,
            "log_tail": [],
        },
        "runtime": {
            "owned": running,
            "lifecycle": "ready" if running else "idle",
            "active_generations": 0,
            "release_pending": False,
        },
        "capabilities": (
            {
                "binary": "/active/llama-server",
                "version": "version: 9999 (active123)",
                "supports_router": True,
            }
            if running
            else None
        ),
    }


def test_server_status_idle_preflight_is_visible_bounded_and_machine_copyable(
    node_package, monkeypatch, tmp_path
):
    node_class = node_package.NODE_CLASS_MAPPINGS["LlamaCppServerStatus"]
    module = sys.modules[node_class.__module__]
    node = node_class()
    present = tmp_path / "present"
    missing = tmp_path / "missing"
    bundle = present / "vision"
    bundle.mkdir(parents=True)
    (present / "text.gguf").write_bytes(b"x")
    (bundle / "vision.gguf").write_bytes(b"x")
    (bundle / "mmproj-vision.gguf").write_bytes(b"x")
    catalog_class = module.ModelCatalog
    calls = {"binary": [], "devices": []}

    class FakeManager:
        def get_status_info(self):
            return _status_data(running=False)

    class FakeCapabilities:
        path = "/resolved/llama-server"
        version_line = "version: 9999 (abcdef123)"
        build_number = 9999
        commit = "abcdef123"
        supports_router = True

        @staticmethod
        def supports(flag):
            return flag == "--list-devices"

    def probe_binary(path, *, timeout):
        calls["binary"].append((path, timeout))
        return FakeCapabilities()

    def probe_devices(path, *, timeout):
        calls["devices"].append((path, timeout))
        return ("CUDA0: Test GPU (1024 MiB)",)

    monkeypatch.setattr(module, "get_server_manager", lambda: FakeManager())
    monkeypatch.setattr(module, "ModelCatalog", lambda: catalog_class([present, missing]))
    monkeypatch.setattr(module, "probe_server_binary", probe_binary)
    monkeypatch.setattr(module, "probe_server_devices", probe_devices)

    output = node.get_status("/chosen/llama-server")
    result = output["result"]
    info = result[2]
    schema = node_class.INPUT_TYPES()

    assert list(schema["optional"]) == ["binary_path"]
    assert schema["optional"]["binary_path"][1]["default"] == ""
    assert len(result) == 3
    assert result[:2] == (False, "stopped")
    assert info.startswith("Status: stopped\nMode: single_model\nOwned: False\nLifecycle: idle\n")
    assert "Setup: warning" in info
    assert "2 models, 1 projectors" in info
    assert "roots 2 configured, 1 present, 1 populated" in info
    assert "2 router presets" in info
    assert "Projector compatibility: not inferred" in info
    assert "Setup warning: 1 configured model root(s) do not exist" in info
    assert output["ui"]["text"] == (info,)
    assert calls == {
        "binary": [("/chosen/llama-server", module._SETUP_PROBE_TIMEOUT)],
        "devices": [("/resolved/llama-server", module._SETUP_PROBE_TIMEOUT)],
    }

    compact = info.split("Diagnostics JSON: ", 1)[1]
    payload = json.loads(compact)
    assert "\n" not in compact
    assert payload["runtime"]["status"] == "stopped"
    assert payload["setup"]["catalog"] == {
        "entries": 3,
        "models": 2,
        "projectors": 1,
        "roots_configured": 2,
        "roots_present": 1,
        "roots_populated": 1,
        "router_presets": 2,
    }
    assert payload["setup"]["projector_compatibility_inferred"] is False


def test_server_status_running_uses_active_binary_without_extra_probe(
    node_package, monkeypatch, tmp_path
):
    node_class = node_package.NODE_CLASS_MAPPINGS["LlamaCppServerStatus"]
    module = sys.modules[node_class.__module__]
    node = node_class()
    (tmp_path / "model.gguf").write_bytes(b"x")
    catalog_class = module.ModelCatalog

    class FakeManager:
        def get_status_info(self):
            return _status_data(running=True)

    def unexpected_probe(*args, **kwargs):
        raise AssertionError(f"active status invoked an idle probe: {args}, {kwargs}")

    monkeypatch.setattr(module, "get_server_manager", lambda: FakeManager())
    monkeypatch.setattr(module, "ModelCatalog", lambda: catalog_class([tmp_path]))
    monkeypatch.setattr(module, "probe_server_binary", unexpected_probe)
    monkeypatch.setattr(module, "probe_server_devices", unexpected_probe)

    output = node.get_status()
    result = output["result"]
    info = result[2]

    assert result[:2] == (True, "running")
    assert "Setup: active" in info
    assert "Binary: /active/llama-server" in info
    assert "Offload devices: not probed while the managed runtime is active" in info
    assert "URL: http://127.0.0.1:8080" in info
    assert "PID: 1234" in info
    assert "Windows Job Object: assigned" in info


def test_server_status_missing_binary_has_one_actionable_bounded_warning(
    node_package, monkeypatch, tmp_path
):
    node_class = node_package.NODE_CLASS_MAPPINGS["LlamaCppServerStatus"]
    module = sys.modules[node_class.__module__]
    node = node_class()
    (tmp_path / "model.gguf").write_bytes(b"x")
    catalog_class = module.ModelCatalog

    class FakeManager:
        def get_status_info(self):
            return _status_data(running=False)

    def missing_binary(*args, **kwargs):
        del args, kwargs
        raise module.BinaryResolutionError("missing " + "x" * 1000)

    monkeypatch.setattr(module, "get_server_manager", lambda: FakeManager())
    monkeypatch.setattr(module, "ModelCatalog", lambda: catalog_class([tmp_path]))
    monkeypatch.setattr(module, "probe_server_binary", missing_binary)
    monkeypatch.setattr(module, "probe_server_devices", lambda *args, **kwargs: ())

    info = node.get_status()["result"][2]
    payload = json.loads(info.split("Diagnostics JSON: ", 1)[1])

    assert "Setup: needs attention" in info
    assert "Binary: not resolved" in info
    assert payload["setup"]["warnings"] == [
        "llama-server is not ready: set Binary Path, LLAMA_SERVER_BINARY or "
        "LLAMA_CPP_SERVER, or add llama-server to PATH."
    ]
    assert len(payload["setup"]["binary"]["error"]) <= module._MAX_WARNING_LENGTH
