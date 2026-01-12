"""
Basic Prompt Node
Sends a prompt to the llama-server and returns the response.
"""

import json
import requests
from typing import Optional

from ..server_manager import get_server_manager

# Try to import ComfyUI's interrupt handling
try:
    import comfy.model_management
    HAS_COMFY_INTERRUPT = True
except ImportError:
    HAS_COMFY_INTERRUPT = False


class LlamaCppBasicPrompt:
    """
    ComfyUI node that sends a prompt to the llama-server and returns the response.
    Supports thinking/reasoning models and various sampling parameters.
    """
    
    CATEGORY = "LlamaCpp"
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("response", "thinking")
    FUNCTION = "generate"
    
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "placeholder": "Enter your prompt here...",
                    "tooltip": "The user prompt to send to the LLM"
                }),
            },
            "optional": {
                "model": ("STRING", {
                    "default": "",
                    "placeholder": "model.gguf (router mode only)",
                    "tooltip": "Model to use in router mode. Leave empty for single-model mode."
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
            }
        }
    
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
    ):
        """Generate a response from the LLM"""

        # Validate prompt
        if not prompt.strip():
            return ("", "")

        # Determine server URL
        if not server_url.strip():
            manager = get_server_manager()
            if not manager.is_running:
                error_msg = "Error: No server running. Use 'Start LlamaCpp Server' or 'Start LlamaCpp Router' first."
                print(f"[LlamaCpp] {error_msg}")
                return (error_msg, "")
            server_url = manager.server_url

        # Build messages
        messages = []
        if system_prompt.strip():
            messages.append({"role": "system", "content": system_prompt.strip()})
        messages.append({"role": "user", "content": prompt})

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
        }

        # Add model if specified (for router mode)
        if model.strip():
            payload["model"] = model.strip()

        # Add seed
        payload["seed"] = seed

        # Add thinking mode
        payload["chat_template_kwargs"] = {
            "enable_thinking": enable_thinking
        }

        endpoint = f"{server_url}/v1/chat/completions"

        print(f"[LlamaCpp] Generating response...")
        if model.strip():
            print(f"[LlamaCpp] Model: {model.strip()}")
        print(f"[LlamaCpp] Thinking mode: {'ON' if enable_thinking else 'OFF'}")
        
        try:
            response = requests.post(
                endpoint,
                json=payload,
                stream=True,
                timeout=300
            )
            response.raise_for_status()
            
            full_response = ""
            thinking_content = ""
            
            for line in response.iter_lines():
                # Check for interrupt
                if HAS_COMFY_INTERRUPT:
                    try:
                        comfy.model_management.throw_exception_if_processing_interrupted()
                    except comfy.model_management.InterruptProcessingException:
                        print("[LlamaCpp] Generation interrupted by user")
                        response.close()
                        raise
                
                if line:
                    decoded = line.decode('utf-8')
                    if decoded.startswith('data: '):
                        json_str = decoded[6:]
                        
                        if json_str.strip() == '[DONE]':
                            break
                        
                        try:
                            chunk = json.loads(json_str)
                            
                            if 'choices' in chunk and len(chunk['choices']) > 0:
                                delta = chunk['choices'][0].get('delta', {})
                                
                                # Handle thinking/reasoning content
                                reasoning = delta.get('reasoning_content', '')
                                if reasoning:
                                    thinking_content += reasoning
                                
                                # Handle regular content
                                content = delta.get('content', '')
                                if content:
                                    full_response += content
                                    
                        except json.JSONDecodeError:
                            pass
            
            # Clean up leading/trailing whitespace
            full_response = full_response.strip()
            thinking_content = thinking_content.strip()
            
            print(f"[LlamaCpp] Generation complete")
            if thinking_content:
                print(f"[LlamaCpp] Thinking: {len(thinking_content)} chars")
            print(f"[LlamaCpp] Response: {len(full_response)} chars")
            
            return (full_response, thinking_content)
            
        except requests.exceptions.ConnectionError:
            error_msg = f"Error: Could not connect to server at {server_url}"
            print(f"[LlamaCpp] {error_msg}")
            return (error_msg, "")
        
        except requests.exceptions.Timeout:
            error_msg = "Error: Request timed out (300s)"
            print(f"[LlamaCpp] {error_msg}")
            return (error_msg, "")
        
        except requests.exceptions.HTTPError as e:
            error_body = ""
            try:
                error_body = e.response.text[:500]
            except:
                pass
            error_msg = f"Error: HTTP {e.response.status_code}"
            if error_body:
                error_msg += f" - {error_body}"
            print(f"[LlamaCpp] {error_msg}")
            return (error_msg, "")
        
        except Exception as e:
            if HAS_COMFY_INTERRUPT and "InterruptProcessingException" in str(type(e)):
                raise
            error_msg = f"Error: {e}"
            print(f"[LlamaCpp] {error_msg}")
            return (error_msg, "")


NODE_CLASS_MAPPINGS = {
    "LlamaCppBasicPrompt": LlamaCppBasicPrompt
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "LlamaCppBasicPrompt": "LlamaCpp Basic Prompt"
}
