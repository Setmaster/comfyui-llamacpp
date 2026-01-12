"""
Prompt Output Node
Displays prompt text in the UI with optional plaintext conversion.
"""

import re
import html


class LlamaCppPromptOutput:
    """
    ComfyUI node that displays prompt/response text in the UI.
    Similar to Preview as Text but with optional plaintext conversion.
    Outputs the text (converted or not) for chaining to other nodes.
    """

    CATEGORY = "LlamaCpp"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)
    FUNCTION = "preview_text"
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "text": ("STRING", {
                    "forceInput": True,
                    "tooltip": "Text to display and optionally convert"
                }),
            },
            "optional": {
                "convert_to_plaintext": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "Convert markdown/HTML to plaintext before displaying"
                }),
            }
        }

    def preview_text(self, text: str, convert_to_plaintext: bool = False):
        """Display text in the UI and pass through"""

        if not text:
            return {"ui": {"text": [""]}, "result": ("",)}

        output_text = text

        if convert_to_plaintext:
            output_text = self._convert_to_plaintext(text)

        # Return both UI display and output value
        return {"ui": {"text": [output_text]}, "result": (output_text,)}

    def _convert_to_plaintext(self, text: str) -> str:
        """Convert markdown/HTML to plaintext"""

        result = text

        # Decode HTML entities
        result = html.unescape(result)

        # Remove HTML tags
        result = re.sub(r'<[^>]+>', '', result)

        # Convert markdown headers to plain text
        result = re.sub(r'^#{1,6}\s+', '', result, flags=re.MULTILINE)

        # Remove markdown bold/italic
        result = re.sub(r'\*\*(.+?)\*\*', r'\1', result)  # Bold
        result = re.sub(r'\*(.+?)\*', r'\1', result)  # Italic
        result = re.sub(r'__(.+?)__', r'\1', result)  # Bold
        result = re.sub(r'_(.+?)_', r'\1', result)  # Italic

        # Remove markdown links, keep text
        result = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', result)

        # Remove markdown images
        result = re.sub(r'!\[([^\]]*)\]\([^)]+\)', r'\1', result)

        # Remove markdown code blocks (keep content)
        result = re.sub(r'```[\w]*\n?', '', result)
        result = re.sub(r'`([^`]+)`', r'\1', result)

        # Remove markdown blockquotes
        result = re.sub(r'^>\s+', '', result, flags=re.MULTILINE)

        # Remove markdown horizontal rules
        result = re.sub(r'^[-*_]{3,}\s*$', '', result, flags=re.MULTILINE)

        # Remove markdown list markers
        result = re.sub(r'^[\s]*[-*+]\s+', '', result, flags=re.MULTILINE)
        result = re.sub(r'^[\s]*\d+\.\s+', '', result, flags=re.MULTILINE)

        # Clean up extra whitespace
        result = re.sub(r'\n{3,}', '\n\n', result)
        result = result.strip()

        return result


NODE_CLASS_MAPPINGS = {
    "LlamaCppPromptOutput": LlamaCppPromptOutput
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "LlamaCppPromptOutput": "llama.cpp Prompt Output"
}
