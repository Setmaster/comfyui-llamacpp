"""
Token Ban Node
Allows users to specify tokens/words to ban from LLM generation via logit_bias.
"""


class LlamaCppTokenBan:
    """
    ComfyUI node that creates a logit_bias list to ban specific tokens/words.
    Outputs a LOGIT_BIAS that can be connected to prompt nodes.
    """

    CATEGORY = "LlamaCpp"
    RETURN_TYPES = ("LOGIT_BIAS",)
    RETURN_NAMES = ("logit_bias",)
    FUNCTION = "create_ban_list"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "banned_tokens": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "placeholder": "word1, word2, phrase to ban, ...",
                    "tooltip": "Comma-separated list of words or phrases to ban from generation"
                }),
                "enable": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "Enable or disable token banning"
                }),
            }
        }

    def create_ban_list(self, banned_tokens: str, enable: bool):
        """Parse banned tokens and create logit_bias entries"""
        if not enable or not banned_tokens.strip():
            return (None,)

        # Parse comma-separated tokens, strip whitespace, filter empty
        tokens = [t.strip() for t in banned_tokens.split(",") if t.strip()]

        if not tokens:
            return (None,)

        # Build logit_bias using llama-server's string format: [["text", false]]
        logit_bias = [[token, False] for token in tokens]

        print(f"[llama.cpp] Token ban: {len(tokens)} entries - {tokens}")

        return (logit_bias,)


NODE_CLASS_MAPPINGS = {
    "LlamaCppTokenBan": LlamaCppTokenBan
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "LlamaCppTokenBan": "llama.cpp Token Ban"
}
