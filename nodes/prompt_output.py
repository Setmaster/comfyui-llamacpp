"""Pass-through prompt preview with conservative plaintext conversion."""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

from .presentation import NODE_CATEGORIES, NODE_SEARCH_ALIASES, apply_input_presentation


class _HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs):
        if tag in {"script", "style"}:
            self._ignored_depth += 1
        elif tag in {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str):
        if tag in {"script", "style"} and self._ignored_depth:
            self._ignored_depth -= 1
        elif tag in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n")

    def handle_data(self, data: str):
        if not self._ignored_depth:
            self.parts.append(data)


class LlamaCppPromptOutput:
    DESCRIPTION = (
        "Displays and forwards LLM text, with optional conversion of common Markdown and "
        "HTML to plaintext."
    )
    CATEGORY = NODE_CATEGORIES["LlamaCppPromptOutput"]
    SEARCH_ALIASES = NODE_SEARCH_ALIASES["LlamaCppPromptOutput"]
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)
    OUTPUT_TOOLTIPS = ("Displayed text, converted to plaintext when requested.",)
    FUNCTION = "preview_text"
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        schema = {
            "required": {
                "text": (
                    "STRING",
                    {"forceInput": True, "tooltip": "Text to display and pass through."},
                )
            },
            "optional": {
                "plaintext": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "tooltip": "Convert common Markdown and HTML markup to plaintext.",
                    },
                )
            },
        }
        return apply_input_presentation("LlamaCppPromptOutput", schema)

    def preview_text(self, text: str, plaintext: bool = False):
        output_text = self._convert_to_plaintext(text) if plaintext and text else (text or "")
        return {"ui": {"text": (output_text,)}, "result": (output_text,)}

    def _convert_to_plaintext(self, text: str) -> str:
        # Images must be handled before links because image syntax contains link syntax.
        result = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
        result = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", result)
        result = re.sub(r"```[^\n]*\n?", "", result)
        result = re.sub(r"`([^`]+)`", r"\1", result)
        result = re.sub(r"^#{1,6}\s+", "", result, flags=re.MULTILINE)
        result = re.sub(
            r"\*\*(.+?)\*\*|__(.+?)__", lambda match: match.group(1) or match.group(2), result
        )
        result = re.sub(
            r"(?<!\*)\*([^*]+)\*|(?<!_)_([^_]+)_",
            lambda match: match.group(1) or match.group(2),
            result,
        )
        result = re.sub(r"^>\s?", "", result, flags=re.MULTILINE)
        result = re.sub(r"^\s*(?:[-*+]\s+|\d+\.\s+)", "", result, flags=re.MULTILINE)
        result = re.sub(r"^\s*[-*_]{3,}\s*$", "", result, flags=re.MULTILINE)

        parser = _HTMLTextExtractor()
        parser.feed(html.unescape(result))
        parser.close()
        result = "".join(parser.parts)
        result = re.sub(r"[ \t]+\n", "\n", result)
        result = re.sub(r"\n{3,}", "\n\n", result)
        return result.strip()


NODE_CLASS_MAPPINGS = {"LlamaCppPromptOutput": LlamaCppPromptOutput}
NODE_DISPLAY_NAME_MAPPINGS = {"LlamaCppPromptOutput": "llama.cpp Prompt Output"}
