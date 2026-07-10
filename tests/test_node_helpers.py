from __future__ import annotations

import importlib
import sys
from contextlib import contextmanager

import pytest


def test_token_ban_supports_json_entries_containing_commas(node_package):
    node = node_package.NODE_CLASS_MAPPINGS["LlamaCppTokenBan"]()
    value = node.create_ban_list('["one,two", " three "]', True)[0]
    assert value == [["one,two", False], [" three ", False]]


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
