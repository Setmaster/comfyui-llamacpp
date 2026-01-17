"""
Streaming Client Utility
Handles streaming requests to llama-server with robust error handling.
"""

import json
import requests
from typing import Tuple, Optional, Callable
from dataclasses import dataclass

# Try to import ComfyUI's interrupt handling
try:
    import comfy.model_management
    HAS_COMFY_INTERRUPT = True
except ImportError:
    HAS_COMFY_INTERRUPT = False


@dataclass
class StreamingResult:
    """Result from a streaming generation request"""
    response: str
    thinking: str
    success: bool
    error_message: Optional[str] = None


def check_interrupt() -> bool:
    """Check if ComfyUI has requested an interrupt. Returns True if interrupted."""
    if HAS_COMFY_INTERRUPT:
        try:
            comfy.model_management.throw_exception_if_processing_interrupted()
        except comfy.model_management.InterruptProcessingException:
            return True
    return False


def parse_server_error(error_text: str) -> str:
    """Parse server error response and extract a readable message"""
    try:
        error_data = json.loads(error_text)
        if isinstance(error_data, dict):
            if 'error' in error_data:
                error_obj = error_data['error']
                if isinstance(error_obj, dict):
                    msg = error_obj.get('message', '')
                    code = error_obj.get('code', '')
                    error_type = error_obj.get('type', '')

                    # Build readable error
                    if 'model' in msg.lower() and 'not found' in msg.lower():
                        return f"Model not found: {msg}"
                    elif 'parse error' in msg.lower():
                        return f"Server request parse error: {msg}"
                    elif 'loading' in msg.lower():
                        return f"Model loading error: {msg}"
                    else:
                        return f"Server error ({code}): {msg}"
                else:
                    return f"Server error: {error_obj}"
    except json.JSONDecodeError:
        pass

    # Return truncated raw error if parsing failed
    if len(error_text) > 200:
        return f"Server error: {error_text[:200]}..."
    return f"Server error: {error_text}"


def stream_generate(
    endpoint: str,
    payload: dict,
    timeout: int = 300,
    chunk_timeout: int = 60,
    on_chunk: Optional[Callable[[str, str], None]] = None,
) -> StreamingResult:
    """
    Send a streaming generation request to the llama-server.

    Args:
        endpoint: Full URL to the chat completions endpoint
        payload: Request payload (messages, model, etc.)
        timeout: Overall request timeout in seconds
        chunk_timeout: Timeout for reading individual chunks (detects hung server)
        on_chunk: Optional callback for progress updates (response_chunk, thinking_chunk)

    Returns:
        StreamingResult with response, thinking content, success status, and error message
    """
    full_response = ""
    thinking_content = ""

    try:
        # Make the streaming request
        response = requests.post(
            endpoint,
            json=payload,
            stream=True,
            timeout=(30, chunk_timeout)  # (connect timeout, read timeout)
        )

        # Check for HTTP errors first
        if response.status_code != 200:
            error_text = ""
            try:
                error_text = response.text[:1000]
            except:
                pass

            error_msg = parse_server_error(error_text) if error_text else f"HTTP {response.status_code}"
            print(f"[llama.cpp] {error_msg}")
            return StreamingResult(
                response=error_msg,
                thinking="",
                success=False,
                error_message=error_msg
            )

        # Process the stream
        for line in response.iter_lines():
            # Check for interrupt
            if check_interrupt():
                print("[llama.cpp] Generation interrupted by user")
                response.close()
                if HAS_COMFY_INTERRUPT:
                    raise comfy.model_management.InterruptProcessingException()
                return StreamingResult(
                    response="Generation interrupted",
                    thinking=thinking_content,
                    success=False,
                    error_message="Interrupted by user"
                )

            if not line:
                continue

            decoded = line.decode('utf-8')

            # Check for error responses in the stream
            if decoded.startswith('{') and '"error"' in decoded:
                try:
                    error_data = json.loads(decoded)
                    if 'error' in error_data:
                        error_msg = parse_server_error(decoded)
                        print(f"[llama.cpp] {error_msg}")
                        response.close()
                        return StreamingResult(
                            response=error_msg,
                            thinking=thinking_content,
                            success=False,
                            error_message=error_msg
                        )
                except json.JSONDecodeError:
                    pass

            if decoded.startswith('data: '):
                json_str = decoded[6:]

                if json_str.strip() == '[DONE]':
                    break

                try:
                    chunk = json.loads(json_str)

                    # Check for error in chunk
                    if 'error' in chunk:
                        error_msg = parse_server_error(json_str)
                        print(f"[llama.cpp] {error_msg}")
                        response.close()
                        return StreamingResult(
                            response=error_msg,
                            thinking=thinking_content,
                            success=False,
                            error_message=error_msg
                        )

                    if 'choices' in chunk and len(chunk['choices']) > 0:
                        delta = chunk['choices'][0].get('delta', {})

                        # Handle thinking/reasoning content
                        reasoning = delta.get('reasoning_content', '')
                        if reasoning:
                            thinking_content += reasoning
                            if on_chunk:
                                on_chunk('', reasoning)

                        # Handle regular content
                        content = delta.get('content', '')
                        if content:
                            full_response += content
                            if on_chunk:
                                on_chunk(content, '')

                except json.JSONDecodeError:
                    # Could be a malformed chunk, log but continue
                    if json_str.strip() and not json_str.strip().startswith(':'):
                        print(f"[llama.cpp] Warning: Could not parse chunk: {json_str[:100]}")

        # Clean up
        full_response = full_response.strip()
        thinking_content = thinking_content.strip()

        # Check if we got any response
        if not full_response and not thinking_content:
            # Could indicate server hung up without producing output
            error_msg = "No response received from server (model may have failed to load)"
            print(f"[llama.cpp] {error_msg}")
            return StreamingResult(
                response=error_msg,
                thinking="",
                success=False,
                error_message=error_msg
            )

        print(f"[llama.cpp] Generation complete")
        if thinking_content:
            print(f"[llama.cpp] Thinking: {len(thinking_content)} chars")
        print(f"[llama.cpp] Response: {len(full_response)} chars")

        return StreamingResult(
            response=full_response,
            thinking=thinking_content,
            success=True
        )

    except requests.exceptions.ConnectionError as e:
        error_str = str(e)

        # Check for specific connection issues
        if 'Connection refused' in error_str:
            error_msg = "Connection refused - server may have crashed or is not running"
        elif 'Connection reset' in error_str:
            error_msg = "Connection reset - server crashed during request (check model compatibility)"
        elif 'RemoteDisconnected' in error_str or 'Remote end closed' in error_str:
            error_msg = "Server disconnected unexpectedly (model may have failed to load)"
        else:
            error_msg = f"Connection error: {error_str[:200]}"

        print(f"[llama.cpp] {error_msg}")
        return StreamingResult(
            response=error_msg,
            thinking=thinking_content,
            success=False,
            error_message=error_msg
        )

    except requests.exceptions.ReadTimeout:
        error_msg = f"Server response timeout ({chunk_timeout}s) - server may be hung or model loading slowly"
        print(f"[llama.cpp] {error_msg}")
        return StreamingResult(
            response=error_msg,
            thinking=thinking_content,
            success=False,
            error_message=error_msg
        )

    except requests.exceptions.Timeout:
        error_msg = f"Request timeout ({timeout}s) - server not responding"
        print(f"[llama.cpp] {error_msg}")
        return StreamingResult(
            response=error_msg,
            thinking=thinking_content,
            success=False,
            error_message=error_msg
        )

    except requests.exceptions.HTTPError as e:
        error_body = ""
        try:
            error_body = e.response.text[:500]
        except:
            pass

        if error_body:
            error_msg = parse_server_error(error_body)
        else:
            error_msg = f"HTTP error {e.response.status_code}"

        print(f"[llama.cpp] {error_msg}")
        return StreamingResult(
            response=error_msg,
            thinking="",
            success=False,
            error_message=error_msg
        )

    except Exception as e:
        # Re-raise interrupt exceptions
        if HAS_COMFY_INTERRUPT and "InterruptProcessingException" in str(type(e)):
            raise

        error_msg = f"Unexpected error: {str(e)[:200]}"
        print(f"[llama.cpp] {error_msg}")
        return StreamingResult(
            response=error_msg,
            thinking=thinking_content,
            success=False,
            error_message=error_msg
        )
