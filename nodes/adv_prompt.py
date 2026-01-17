"""
Advanced Prompt Node
Extends Basic Prompt with vision/image support for VLM models.
"""

import io
import base64
import numpy as np
from PIL import Image
from typing import List

from ..server_manager import get_server_manager
from ..model_manager import get_local_models
from ..streaming_client import stream_generate


class LlamaCppAdvPrompt:
    """
    ComfyUI node that sends a prompt with images to the llama-server.
    Supports VLM (Vision Language Models) with dynamic image inputs.
    """

    CATEGORY = "LlamaCpp"
    RETURN_TYPES = ("STRING", "STRING", "BOOLEAN")
    RETURN_NAMES = ("response", "thinking", "success")
    FUNCTION = "generate"

    # Maximum number of images supported
    MAX_IMAGES = 10

    @classmethod
    def INPUT_TYPES(cls):
        # Get available models for dropdown (already excludes mmproj)
        local_models = get_local_models()

        # Add empty option at the start for single-model mode
        model_choices = ["(use running model)"] + local_models

        inputs = {
            "required": {
                "prompt": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "placeholder": "Enter your prompt here...",
                    "tooltip": "The user prompt to send to the LLM"
                }),
                "image_amount": ("INT", {
                    "default": 2,
                    "min": 0,
                    "max": cls.MAX_IMAGES,
                    "step": 1,
                    "tooltip": "Number of image input slots to show"
                }),
            },
            "optional": {
                "model": (model_choices, {
                    "default": "(use running model)",
                    "tooltip": "Model to use. Select a model for router mode, or '(use running model)' for single-model mode."
                }),
                "server_url": ("STRING", {
                    "default": "",
                    "placeholder": "http://127.0.0.1:8080",
                    "tooltip": "Server URL. Leave empty to use running server."
                }),
                "system_prompt": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "placeholder": "Optional system prompt...",
                    "tooltip": "System prompt to set the AI's behavior"
                }),
                "enable_thinking": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "Enable thinking/reasoning mode (for supported models)"
                }),
                "max_tokens": ("INT", {
                    "default": 2048,
                    "min": 1,
                    "max": 32768,
                    "step": 64,
                    "tooltip": "Maximum tokens to generate"
                }),
                "temperature": ("FLOAT", {
                    "default": 0.7,
                    "min": 0.0,
                    "max": 2.0,
                    "step": 0.05,
                    "tooltip": "Sampling temperature. Higher = more creative, lower = more focused."
                }),
                "top_p": ("FLOAT", {
                    "default": 0.9,
                    "min": 0.0,
                    "max": 1.0,
                    "step": 0.05,
                    "tooltip": "Top-p (nucleus) sampling threshold"
                }),
                "top_k": ("INT", {
                    "default": 40,
                    "min": 0,
                    "max": 200,
                    "step": 1,
                    "tooltip": "Top-k sampling. 0 = disabled."
                }),
                "min_p": ("FLOAT", {
                    "default": 0.05,
                    "min": 0.0,
                    "max": 1.0,
                    "step": 0.01,
                    "tooltip": "Min-p sampling threshold"
                }),
                "repeat_penalty": ("FLOAT", {
                    "default": 1.1,
                    "min": 1.0,
                    "max": 2.0,
                    "step": 0.05,
                    "tooltip": "Repetition penalty. 1.0 = no penalty."
                }),
                "seed": ("INT", {
                    "default": 0,
                    "min": 0,
                    "max": 0x7FFFFFFF,
                    "tooltip": "Random seed for generation"
                }),
                "keep_context": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "Keep conversation context between requests. When OFF, each request starts fresh."
                }),
                "enable_chaining": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "Enable chaining mode. When enabled, waits for trigger input before executing."
                }),
                "trigger": ("*", {
                    "tooltip": "Optional trigger input for chaining. Connect to another node's output to sequence execution."
                }),
            }
        }

        # Note: Image inputs (image_1, image_2, etc.) are created dynamically
        # by the JavaScript extension based on the image_amount widget.
        # They are captured via **kwargs in the generate() method.

        return inputs

    def _tensor_to_base64(self, tensor) -> str:
        """Convert a ComfyUI image tensor to base64 data URL"""
        # ComfyUI tensors are [B, H, W, C] in range [0, 1]
        # Take first image if batched
        if len(tensor.shape) == 4:
            tensor = tensor[0]

        # Convert to numpy and scale to 0-255
        img_np = (tensor.cpu().numpy() * 255).astype(np.uint8)

        # Create PIL Image
        pil_img = Image.fromarray(img_np)

        # Convert to base64
        buffer = io.BytesIO()
        pil_img.save(buffer, format="PNG")
        buffer.seek(0)
        base64_str = base64.b64encode(buffer.getvalue()).decode("utf-8")

        return f"data:image/png;base64,{base64_str}"

    def generate(
        self,
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
        seed: int = 0,
        keep_context: bool = False,
        enable_chaining: bool = False,
        trigger=None,
        **kwargs,  # Capture dynamic image inputs
    ):
        """Generate a response from the LLM with optional images"""

        # Validate prompt
        if not prompt.strip():
            return ("", "", False)

        # Get server manager
        manager = get_server_manager()

        # Determine server URL
        if not server_url.strip():
            if not manager.is_running:
                error_msg = "Error: No server running. Use 'Start llama.cpp Server' or 'Start llama.cpp Router' first."
                print(f"[llama.cpp] {error_msg}")
                return (error_msg, "", False)
            server_url = manager.server_url

        # Collect images from kwargs
        images: List[str] = []
        for i in range(1, self.MAX_IMAGES + 1):
            img_key = f"image_{i}"
            if img_key in kwargs and kwargs[img_key] is not None:
                try:
                    base64_url = self._tensor_to_base64(kwargs[img_key])
                    images.append(base64_url)
                    print(f"[llama.cpp] Added image_{i}")
                except Exception as e:
                    print(f"[llama.cpp] Warning: Failed to process {img_key}: {e}")

        # Build user content (text + images for VLM)
        if images:
            # OpenAI-compatible vision format
            user_content = []
            for img_url in images:
                user_content.append({
                    "type": "image_url",
                    "image_url": {"url": img_url}
                })
            user_content.append({
                "type": "text",
                "text": prompt
            })
        else:
            user_content = prompt

        # Build messages
        messages = []
        if system_prompt.strip():
            messages.append({"role": "system", "content": system_prompt.strip()})
        messages.append({"role": "user", "content": user_content})

        # Build payload
        payload = {
            "messages": messages,
            "stream": True,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": top_p,
            "top_k": top_k,
            "min_p": min_p,
            "repeat_penalty": repeat_penalty,
            "cache_prompt": keep_context,  # When False, starts fresh without prior context
        }

        # Add model if specified (for router mode)
        if model and model.strip() and model != "(use running model)":
            model_name = model.strip()

            # In router mode, try to find matching model from server's discovered models
            if manager.is_router_mode:
                success, models, _ = manager.list_models()
                if success and models:
                    # Build candidate names to try matching
                    # Input might be: "qwen-vl/model-name.gguf" or "model-name.gguf"
                    candidates = []

                    # 1. Without .gguf extension
                    name_no_ext = model_name
                    if name_no_ext.lower().endswith('.gguf'):
                        name_no_ext = name_no_ext[:-5]
                    candidates.append(name_no_ext)

                    # 2. Just the filename (without directory and extension)
                    if '/' in name_no_ext:
                        filename_only = name_no_ext.split('/')[-1]
                        candidates.append(filename_only)
                        # 3. Just the directory name (for subdirectory models)
                        dir_name = name_no_ext.split('/')[0]
                        candidates.append(dir_name)

                    # Get all model IDs from server
                    server_model_ids = []
                    for m in models:
                        if isinstance(m, dict):
                            model_id = m.get("id") or m.get("model") or ""
                            if model_id:
                                server_model_ids.append(model_id)

                    # Try to find a match
                    matched_id = None
                    for candidate in candidates:
                        if candidate in server_model_ids:
                            matched_id = candidate
                            break

                    if matched_id:
                        if matched_id != model_name:
                            print(f"[llama.cpp] Mapped model '{model_name}' -> '{matched_id}'")
                        model_name = matched_id
                    else:
                        # No match found - use filename without extension as best guess
                        if '/' in name_no_ext:
                            model_name = name_no_ext.split('/')[-1]
                        else:
                            model_name = name_no_ext
                        print(f"[llama.cpp] No exact match found, using '{model_name}'")
                        print(f"[llama.cpp] Available models: {server_model_ids}")

            payload["model"] = model_name

        # Add seed
        payload["seed"] = seed

        # Add thinking mode
        payload["chat_template_kwargs"] = {
            "enable_thinking": enable_thinking
        }

        endpoint = f"{server_url}/v1/chat/completions"

        print(f"[llama.cpp] Generating response...")
        if "model" in payload:
            print(f"[llama.cpp] Model: {payload['model']}")
        print(f"[llama.cpp] Images: {len(images)}")
        print(f"[llama.cpp] Thinking mode: {'ON' if enable_thinking else 'OFF'}")

        # Use streaming client with improved error handling
        result = stream_generate(
            endpoint=endpoint,
            payload=payload,
            timeout=300,
            chunk_timeout=60,
        )

        return (result.response, result.thinking, result.success)


NODE_CLASS_MAPPINGS = {
    "LlamaCppAdvPrompt": LlamaCppAdvPrompt
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "LlamaCppAdvPrompt": "llama.cpp ADV Prompt"
}
