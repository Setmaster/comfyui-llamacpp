"""Shared prompt execution path for all compatibility node facades."""

from __future__ import annotations

from typing import Any

from ..generation import (
    GenerationOptions,
    apply_template,
    build_chat_payload,
    image_tensor_to_data_urls,
    parse_text_list,
)
from ..runtime.manager import LlamaCppServerManager, get_server_manager
from ..runtime.streaming import StreamResult, stream_chat
from .connection import LlamaCppConnectionProfile
from .schemas import MAX_IMAGES, RUNNING_MODEL


def _interrupt_check() -> bool:
    try:
        import comfy.model_management as model_management
    except ImportError:
        return False
    # This intentionally propagates Comfy's InterruptProcessingException.
    model_management.throw_exception_if_processing_interrupted()
    return False


def collect_images(
    image_amount: int,
    image_inputs: dict[str, Any],
    *,
    include_batch: bool = False,
) -> list[str]:
    count = max(0, min(MAX_IMAGES, int(image_amount)))
    images: list[str] = []
    for index in range(1, count + 1):
        image = image_inputs.get(f"image_{index}")
        if image is not None:
            images.extend(image_tensor_to_data_urls(image, include_batch=include_batch))
    return images


def _profile_values(
    connection: LlamaCppConnectionProfile | None,
    *,
    server_url: str,
    model: str,
    api_key_env: str,
    verify_tls: bool,
    request_timeout: int,
) -> tuple[str, str, str, bool, int]:
    if connection is None:
        return server_url, model, api_key_env, verify_tls, request_timeout
    return (
        connection.server_url or server_url,
        connection.model or model,
        connection.api_key_env,
        connection.verify_tls,
        connection.request_timeout,
    )


def run_prompt(
    prompt: str,
    *,
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
    stop_sequences: str = "",
    images: list[str] | None = None,
    token_ban: Any = None,
    structured_output: Any = None,
    template: str = "Empty",
    api_key_env: str = "LLAMACPP_API_KEY",
    verify_tls: bool = True,
    request_timeout: int = 300,
    connection: LlamaCppConnectionProfile | None = None,
    manager: LlamaCppServerManager | None = None,
) -> StreamResult:
    manager = manager or get_server_manager()
    server_url, model, api_key_env, verify_tls, request_timeout = _profile_values(
        connection,
        server_url=server_url,
        model=model,
        api_key_env=api_key_env,
        verify_tls=verify_tls,
        request_timeout=request_timeout,
    )

    try:
        prompt, system_prompt = apply_template(template, prompt, system_prompt)
        connection_config, managed = manager.connection_for(
            server_url,
            api_key_env=api_key_env,
            verify_tls=verify_tls,
            request_timeout=request_timeout,
        )
        selected_model = model.strip()
        with manager.generation_lease(managed=managed):
            if managed and not manager.is_running:
                raise RuntimeError("The owned llama-server was released before generation began")
            if selected_model and selected_model != RUNNING_MODEL and manager.is_router_mode:
                selected_model = manager.resolve_model_id(selected_model)
            options = GenerationOptions(
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
                min_p=min_p,
                repeat_penalty=repeat_penalty,
                presence_penalty=presence_penalty,
                frequency_penalty=frequency_penalty,
                seed=seed,
                cache_prompt=keep_context,
                enable_thinking=enable_thinking,
                stop=tuple(parse_text_list(stop_sequences)),
            )
            payload = build_chat_payload(
                prompt,
                system_prompt=system_prompt,
                images=images or (),
                options=options,
                model=selected_model,
                logit_bias=token_ban,
                structured_output=structured_output,
            )
            return stream_chat(
                connection_config,
                payload,
                timeout=request_timeout,
                chunk_timeout=min(60, request_timeout),
                cancel=_interrupt_check,
            )
    except Exception as exc:
        # Comfy interrupt exceptions inherit BaseException and bypass this block.
        message = f"{type(exc).__name__}: {exc}"
        return StreamResult("", "", False, error_message=message, error_type="client")


def legacy_result(result: StreamResult) -> tuple[str, str, bool]:
    if result.success:
        return result.response, result.thinking, True
    error = result.error_message or "generation failed"
    if result.response:
        response = f"{result.response}\n\n[Generation incomplete: {error}]"
    else:
        response = f"Error: {error}"
    return response, result.thinking, False
