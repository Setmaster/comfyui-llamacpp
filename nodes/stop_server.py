"""
Stop LlamaCpp Server Node
Stops the running llama-server.
"""

from ..server_manager import get_server_manager


class StopLlamaCppServer:
    """
    ComfyUI node that stops the llama.cpp server.
    Does nothing if no server is running.
    """
    
    CATEGORY = "LlamaCpp"
    RETURN_TYPES = ("BOOLEAN", "STRING")
    RETURN_NAMES = ("success", "message")
    FUNCTION = "stop_server"
    OUTPUT_NODE = True
    
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {},
            "optional": {
                "trigger": ("*", {
                    "tooltip": "Optional input to trigger this node in a workflow"
                }),
            }
        }
    
    @classmethod
    def IS_CHANGED(cls, **kwargs):
        # Always execute
        return float("nan")
    
    def stop_server(self, trigger=None):
        """Stop the llama-server"""
        
        manager = get_server_manager()
        
        # Check if server is running
        if not manager.is_running:
            message = "No server running"
            print(f"[llama.cpp] {message}")
            return (True, message)
        
        # Stop the server
        success, error = manager.stop()
        
        if success:
            message = "Server stopped successfully"
            print(f"[llama.cpp] {message}")
            return (True, message)
        else:
            message = f"Error stopping server: {error}"
            print(f"[llama.cpp] {message}")
            return (False, message)


class LlamaCppServerStatus:
    """
    ComfyUI node that returns the current server status.
    Useful for debugging and workflow conditionals.
    """
    
    CATEGORY = "LlamaCpp"
    RETURN_TYPES = ("BOOLEAN", "STRING", "STRING")
    RETURN_NAMES = ("is_running", "status", "info")
    FUNCTION = "get_status"
    
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {},
        }
    
    @classmethod
    def IS_CHANGED(cls, **kwargs):
        # Always re-check
        return float("nan")
    
    def get_status(self):
        """Get the current server status"""

        manager = get_server_manager()
        status_info = manager.get_status_info()

        is_running = status_info["is_running"]
        status = status_info["status"]
        mode = status_info.get("mode", "single_model")

        # Build info string
        info_parts = [f"Status: {status}"]
        info_parts.append(f"Mode: {mode}")

        if is_running:
            info_parts.append(f"URL: {status_info['server_url']}")
            if "config" in status_info:
                config = status_info["config"]
                if mode == "router":
                    info_parts.append(f"Models dir: {config.get('models_dir', 'N/A')}")
                    info_parts.append(f"Max models: {config.get('models_max', 'N/A')}")
                else:
                    info_parts.append(f"Model: {config.get('model', 'N/A')}")
                info_parts.append(f"Context: {config.get('context_size', 'N/A')}")
            if "pid" in status_info:
                info_parts.append(f"PID: {status_info['pid']}")

        if status_info.get("last_error"):
            info_parts.append(f"Last error: {status_info['last_error'][:100]}")

        info = "\n".join(info_parts)

        return (is_running, status, info)


NODE_CLASS_MAPPINGS = {
    "StopLlamaCppServer": StopLlamaCppServer,
    "LlamaCppServerStatus": LlamaCppServerStatus,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "StopLlamaCppServer": "Stop llama.cpp Server",
    "LlamaCppServerStatus": "llama.cpp Server Status",
}
