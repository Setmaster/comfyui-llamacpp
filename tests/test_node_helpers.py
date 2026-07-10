from __future__ import annotations

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


def test_plaintext_conversion_handles_images_links_html_and_ignored_content(node_package):
    node = node_package.NODE_CLASS_MAPPINGS["LlamaCppPromptOutput"]()
    text = "# Title\n![alt](image.png) [link](https://example.com)<br><b>bold</b><script>x</script>"
    output = node.preview_text(text, True)["result"][0]
    assert output == "Title\nalt link\nbold"
