"""Backward-compatible basic llama.cpp prompt node."""

from .common import legacy_result, run_prompt
from .connection import LlamaCppConnectionProfile
from .schemas import basic_prompt_inputs


class LlamaCppBasicPrompt:
    DESCRIPTION = (
        "Runs local text generation through llama-server with sampling, reasoning, "
        "and workflow-chaining controls."
    )
    CATEGORY = "LlamaCpp"
    RETURN_TYPES = ("STRING", "STRING", "BOOLEAN")
    RETURN_NAMES = ("response", "thinking", "success")
    OUTPUT_TOOLTIPS = (
        "Generated response text.",
        "Reasoning content reported separately by compatible models.",
        "Whether generation completed successfully.",
    )
    FUNCTION = "generate"

    @classmethod
    def INPUT_TYPES(cls):
        return basic_prompt_inputs()

    def generate(
        self,
        prompt: str,
        model: str = "",
        server_url: str = "",
        system_prompt: str = "",
        enable_thinking: bool = True,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        top_p: float = 0.9,
        top_k: int = 40,
        min_p: float = 0.05,
        repeat_penalty: float = 1.1,
        seed: int = 0,
        keep_context: bool = False,
        enable_chaining: bool = False,
        trigger=None,
        presence_penalty: float = 0.0,
        frequency_penalty: float = 0.0,
        stop_sequences: str = "",
        api_key_env: str = "LLAMACPP_API_KEY",
        verify_tls: bool = True,
        request_timeout: int = 300,
        connection: LlamaCppConnectionProfile | None = None,
    ):
        del enable_chaining, trigger
        return legacy_result(
            run_prompt(
                prompt,
                model=model,
                server_url=server_url,
                system_prompt=system_prompt,
                enable_thinking=enable_thinking,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
                min_p=min_p,
                repeat_penalty=repeat_penalty,
                presence_penalty=presence_penalty,
                frequency_penalty=frequency_penalty,
                seed=seed,
                keep_context=keep_context,
                stop_sequences=stop_sequences,
                api_key_env=api_key_env,
                verify_tls=verify_tls,
                request_timeout=request_timeout,
                connection=connection,
            )
        )


NODE_CLASS_MAPPINGS = {"LlamaCppBasicPrompt": LlamaCppBasicPrompt}
NODE_DISPLAY_NAME_MAPPINGS = {"LlamaCppBasicPrompt": "llama.cpp Basic Prompt"}
