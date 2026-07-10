"""Backward-compatible full multimodal and structured prompt node."""

from ..generation.templates import get_template_names
from .common import collect_images, legacy_result, run_prompt
from .connection import LlamaCppConnectionProfile
from .schemas import MAX_IMAGES, advanced_pp_prompt_inputs


class LlamaCppAdvPPPrompt:
    CATEGORY = "LlamaCpp"
    RETURN_TYPES = ("STRING", "STRING", "BOOLEAN")
    RETURN_NAMES = ("response", "thinking", "success")
    FUNCTION = "generate"
    MAX_IMAGES = MAX_IMAGES

    @classmethod
    def INPUT_TYPES(cls):
        return advanced_pp_prompt_inputs(get_template_names())

    def generate(
        self,
        template: str,
        prompt: str,
        image_amount: int = 2,
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
        presence_penalty: float = 0.0,
        frequency_penalty: float = 0.0,
        seed: int = 0,
        keep_context: bool = False,
        enable_chaining: bool = False,
        trigger=None,
        token_ban=None,
        enable_token_ban: bool = True,
        stop_sequences: str = "",
        api_key_env: str = "LLAMACPP_API_KEY",
        verify_tls: bool = True,
        request_timeout: int = 300,
        include_image_batch: bool = False,
        image_1=None,
        image_2=None,
        image_3=None,
        image_4=None,
        image_5=None,
        image_6=None,
        image_7=None,
        image_8=None,
        image_9=None,
        image_10=None,
        structured_output=None,
        connection: LlamaCppConnectionProfile | None = None,
        **kwargs,
    ):
        del enable_chaining, trigger, kwargs
        image_inputs = {
            f"image_{index}": value
            for index, value in enumerate(
                (
                    image_1,
                    image_2,
                    image_3,
                    image_4,
                    image_5,
                    image_6,
                    image_7,
                    image_8,
                    image_9,
                    image_10,
                ),
                start=1,
            )
        }
        try:
            images = collect_images(
                image_amount,
                image_inputs,
                include_batch=include_image_batch,
            )
        except Exception as exc:
            return (f"Error: Failed to process image input: {exc}", "", False)
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
                images=images,
                token_ban=token_ban if enable_token_ban else None,
                structured_output=structured_output,
                template=template,
                api_key_env=api_key_env,
                verify_tls=verify_tls,
                request_timeout=request_timeout,
                connection=connection,
            )
        )


NODE_CLASS_MAPPINGS = {"LlamaCppAdvPPPrompt": LlamaCppAdvPPPrompt}
NODE_DISPLAY_NAME_MAPPINGS = {"LlamaCppAdvPPPrompt": "llama.cpp ADV++ Prompt"}
