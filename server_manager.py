"""
LlamaCpp Server Manager
Core module for managing the llama-server process lifecycle.
"""

import os
import subprocess
import time
import atexit
import signal
import sys
import psutil
from typing import Optional, Tuple, Dict, Any, Union, List
from dataclasses import dataclass, field
from enum import Enum


class ServerStatus(Enum):
    """Server status states"""
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    ERROR = "error"


class ServerMode(Enum):
    """Server operation mode"""
    SINGLE_MODEL = "single_model"
    ROUTER = "router"


@dataclass
class ServerConfig:
    """Configuration for the llama-server"""
    model_path: str
    port: int = 8080
    host: str = "127.0.0.1"
    context_size: int = 4096
    n_gpu_layers: int = 999
    main_gpu: int = 0
    tensor_split: Optional[str] = None
    threads: Optional[int] = None
    batch_size: Optional[int] = None
    flash_attention: bool = False
    no_mmap: bool = False
    
    def to_command_args(self) -> list:
        """Convert config to command line arguments"""
        args = [
            "-m", self.model_path,
            "--port", str(self.port),
            "--host", self.host,
            "-c", str(self.context_size),
            "-ngl", str(self.n_gpu_layers),
            "--main-gpu", str(self.main_gpu),
        ]
        
        if self.tensor_split:
            args.extend(["--tensor-split", self.tensor_split])
        
        if self.threads:
            args.extend(["-t", str(self.threads)])
            
        if self.batch_size:
            args.extend(["-b", str(self.batch_size)])
            
        if self.flash_attention:
            args.append("-fa")
            
        if self.no_mmap:
            args.append("--no-mmap")
            
        return args
    
    def config_hash(self) -> str:
        """Generate a hash for config comparison"""
        return f"single:{self.model_path}:{self.port}:{self.context_size}:{self.n_gpu_layers}:{self.main_gpu}:{self.tensor_split}"


@dataclass
class RouterConfig:
    """Configuration for the llama-server in router mode"""
    models_dir: str
    port: int = 8080
    host: str = "127.0.0.1"
    context_size: int = 4096
    n_gpu_layers: int = 999
    main_gpu: int = 0
    threads: Optional[int] = None
    batch_size: Optional[int] = None
    flash_attention: bool = False
    models_max: int = 4  # Max models loaded simultaneously
    models_autoload: bool = True  # Auto-load models on request

    def to_command_args(self) -> list:
        """Convert config to command line arguments for router mode"""
        args = [
            "--models-dir", self.models_dir,
            "--port", str(self.port),
            "--host", self.host,
            "-c", str(self.context_size),
            "-ngl", str(self.n_gpu_layers),
            "--main-gpu", str(self.main_gpu),
            "--models-max", str(self.models_max),
        ]

        if self.threads:
            args.extend(["-t", str(self.threads)])

        if self.batch_size:
            args.extend(["-b", str(self.batch_size)])

        if self.flash_attention:
            args.append("-fa")

        if not self.models_autoload:
            args.append("--no-models-autoload")

        return args

    def config_hash(self) -> str:
        """Generate a hash for config comparison"""
        return f"router:{self.models_dir}:{self.port}:{self.context_size}:{self.n_gpu_layers}:{self.main_gpu}:{self.models_max}"


class LlamaCppServerManager:
    """
    Singleton manager for the llama-server process.
    Handles starting, stopping, and monitoring the server.
    """
    
    _instance: Optional['LlamaCppServerManager'] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return

        self._initialized = True
        self._process: Optional[subprocess.Popen] = None
        self._config: Optional[Union[ServerConfig, RouterConfig]] = None
        self._mode: ServerMode = ServerMode.SINGLE_MODEL
        self._status: ServerStatus = ServerStatus.STOPPED
        self._job_handle = None  # Windows job object
        self._last_error: Optional[str] = None

        # Setup cleanup handlers
        self._setup_cleanup_handlers()

        print("[LlamaCpp] Server manager initialized")
    
    def _setup_cleanup_handlers(self):
        """Setup handlers for clean shutdown"""
        # Register atexit handler
        atexit.register(self._cleanup)
        
        # Setup signal handlers (Unix)
        try:
            signal.signal(signal.SIGTERM, self._signal_handler)
        except Exception:
            pass  # May fail on Windows
        
        # Setup Windows-specific handlers
        if os.name == 'nt':
            self._setup_windows_job_object()
            self._setup_windows_console_handler()
    
    def _setup_windows_job_object(self):
        """Create Windows Job Object for guaranteed child process cleanup"""
        try:
            import ctypes
            from ctypes import wintypes
            
            kernel32 = ctypes.windll.kernel32
            
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
            JobObjectExtendedLimitInformation = 9
            
            self._job_handle = kernel32.CreateJobObjectW(None, None)
            if not self._job_handle:
                return
            
            class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD),
                ]

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("ReadOperationCount", ctypes.c_uint64),
                    ("WriteOperationCount", ctypes.c_uint64),
                    ("OtherOperationCount", ctypes.c_uint64),
                    ("ReadTransferCount", ctypes.c_uint64),
                    ("WriteTransferCount", ctypes.c_uint64),
                    ("OtherTransferCount", ctypes.c_uint64),
                ]

            class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                    ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t),
                ]

            info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

            kernel32.SetInformationJobObject(
                self._job_handle,
                JobObjectExtendedLimitInformation,
                ctypes.byref(info),
                ctypes.sizeof(info)
            )
            print("[LlamaCpp] Windows Job Object created")
            
        except Exception as e:
            print(f"[LlamaCpp] Warning: Job object setup failed: {e}")
    
    def _setup_windows_console_handler(self):
        """Setup Windows console close handler"""
        try:
            import ctypes
            
            CTRL_CLOSE_EVENT = 2
            CTRL_LOGOFF_EVENT = 5
            CTRL_SHUTDOWN_EVENT = 6
            
            @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)
            def console_handler(event):
                if event in (CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT):
                    print(f"\n[LlamaCpp] Console closing, cleaning up...")
                    self._cleanup()
                    return False
                return False
            
            # Keep reference to prevent garbage collection
            self._console_handler_ref = console_handler
            
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleCtrlHandler(console_handler, True)
            print("[LlamaCpp] Console close handler registered")
            
        except Exception as e:
            print(f"[LlamaCpp] Warning: Console handler setup failed: {e}")
    
    def _assign_to_job(self, process: subprocess.Popen):
        """Assign process to Windows job object"""
        if os.name != 'nt' or not self._job_handle or not process:
            return
        
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = int(process._handle)
            kernel32.AssignProcessToJobObject(self._job_handle, handle)
        except Exception:
            pass
    
    def _signal_handler(self, signum, frame):
        """Handle termination signals"""
        print(f"\n[LlamaCpp] Signal {signum} received, cleaning up...")
        self._cleanup()
        sys.exit(0)
    
    def _cleanup(self):
        """Cleanup server process"""
        self.stop()
        self._kill_orphaned_servers()
    
    def _kill_orphaned_servers(self):
        """Kill any orphaned llama-server processes"""
        try:
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    if proc.info['name'] and 'llama-server' in proc.info['name'].lower():
                        print(f"[LlamaCpp] Killing orphaned llama-server (PID: {proc.info['pid']})")
                        proc.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception as e:
            print(f"[LlamaCpp] Error cleaning up orphaned processes: {e}")
    
    @property
    def status(self) -> ServerStatus:
        """Get current server status"""
        if self._process is None:
            return ServerStatus.STOPPED
        
        # Check if process is still running
        poll_result = self._process.poll()
        if poll_result is not None:
            self._status = ServerStatus.STOPPED
            self._process = None
            return ServerStatus.STOPPED
        
        return self._status
    
    @property
    def is_running(self) -> bool:
        """Check if server is running"""
        return self.status == ServerStatus.RUNNING
    
    @property
    def current_config(self) -> Optional[Union[ServerConfig, RouterConfig]]:
        """Get current server configuration"""
        return self._config if self.is_running else None

    @property
    def mode(self) -> ServerMode:
        """Get current server mode"""
        return self._mode

    @property
    def is_router_mode(self) -> bool:
        """Check if server is running in router mode"""
        return self._mode == ServerMode.ROUTER and self.is_running
    
    @property
    def last_error(self) -> Optional[str]:
        """Get last error message"""
        return self._last_error
    
    @property
    def server_url(self) -> str:
        """Get server URL"""
        if self._config:
            return f"http://{self._config.host}:{self._config.port}"
        return "http://127.0.0.1:8080"
    
    def health_check(self) -> bool:
        """Check if server is responding to health endpoint"""
        try:
            import requests
            response = requests.get(f"{self.server_url}/health", timeout=2)
            return response.status_code == 200
        except Exception:
            return False
    
    def start(self, config: ServerConfig, timeout: Optional[int] = 60) -> Tuple[bool, Optional[str]]:
        """
        Start the llama-server with the given configuration.

        Args:
            config: Server configuration
            timeout: Maximum seconds to wait for server startup (None = no timeout)

        Returns:
            Tuple of (success, error_message)
        """
        self._last_error = None
        
        # Check if already running with same config
        if self.is_running and self._config and self._config.config_hash() == config.config_hash():
            if self.health_check():
                print(f"[LlamaCpp] Server already running with same configuration")
                return (True, None)
        
        # Stop existing server if running
        if self._process is not None:
            print("[LlamaCpp] Stopping existing server for reconfiguration...")
            self.stop()
        
        # Kill any orphaned servers
        self._kill_orphaned_servers()
        
        # Validate model path
        if not os.path.exists(config.model_path):
            error = f"Model file not found: {config.model_path}"
            self._last_error = error
            return (False, error)
        
        # Determine server command
        server_cmd = "llama-server.exe" if os.name == 'nt' else "llama-server"
        
        # Build command
        cmd = [server_cmd] + config.to_command_args()
        
        print(f"[LlamaCpp] Starting server...")
        print(f"[LlamaCpp] Model: {os.path.basename(config.model_path)}")
        print(f"[LlamaCpp] Context size: {config.context_size}")
        print(f"[LlamaCpp] Command: {' '.join(cmd)}")
        
        try:
            self._status = ServerStatus.STARTING
            
            # Start process
            if os.name == 'nt':
                self._process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
                )
            else:
                self._process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT
                )
            
            # Assign to job object on Windows
            self._assign_to_job(self._process)
            self._config = config
            self._mode = ServerMode.SINGLE_MODEL

            # Wait for server to be ready
            if timeout is None:
                print(f"[LlamaCpp] Waiting for server to be ready (no timeout)...")
            else:
                print(f"[LlamaCpp] Waiting for server to be ready (timeout: {timeout}s)...")

            elapsed = 0
            while True:
                time.sleep(1)
                elapsed += 1

                if self.health_check():
                    self._status = ServerStatus.RUNNING
                    print(f"[LlamaCpp] Server ready! (took {elapsed}s)")
                    return (True, None)

                # Check if process crashed
                if self._process.poll() is not None:
                    try:
                        output = self._process.stdout.read().decode('utf-8', errors='ignore')
                    except Exception:
                        output = ""

                    error = f"Server crashed during startup (exit code: {self._process.returncode})"
                    if output:
                        error += f"\n\nServer output:\n{output[:2000]}"

                    self._status = ServerStatus.ERROR
                    self._last_error = error
                    self._process = None
                    self._config = None
                    return (False, error)

                if elapsed % 10 == 0:
                    print(f"[LlamaCpp] Still waiting... ({elapsed}s)")

                # Check timeout (if set)
                if timeout is not None and elapsed >= timeout:
                    error = f"Server did not start within {timeout} seconds"
                    self._last_error = error
                    self.stop()
                    return (False, error)
            
        except FileNotFoundError:
            error = "llama-server not found. Please install llama.cpp and add to PATH.\n" \
                   "Installation: https://github.com/ggml-org/llama.cpp/blob/master/docs/install.md"
            self._status = ServerStatus.ERROR
            self._last_error = error
            return (False, error)
        
        except Exception as e:
            error = f"Failed to start server: {e}"
            self._status = ServerStatus.ERROR
            self._last_error = error
            return (False, error)
    
    def stop(self) -> Tuple[bool, Optional[str]]:
        """
        Stop the llama-server.
        
        Returns:
            Tuple of (success, error_message)
        """
        if self._process is None:
            print("[LlamaCpp] No server running")
            return (True, None)
        
        try:
            print("[LlamaCpp] Stopping server...")
            
            # Try graceful termination first
            self._process.terminate()
            
            try:
                self._process.wait(timeout=5)
                print("[LlamaCpp] Server stopped gracefully")
            except subprocess.TimeoutExpired:
                # Force kill if still running
                print("[LlamaCpp] Force killing server...")
                self._process.kill()
                self._process.wait(timeout=3)
                print("[LlamaCpp] Server killed")
            
            self._process = None
            self._config = None
            self._status = ServerStatus.STOPPED
            self._mode = ServerMode.SINGLE_MODEL

            # Also kill any orphaned processes
            self._kill_orphaned_servers()

            return (True, None)

        except Exception as e:
            error = f"Error stopping server: {e}"
            self._last_error = error

            # Force cleanup
            self._process = None
            self._config = None
            self._status = ServerStatus.STOPPED
            self._mode = ServerMode.SINGLE_MODEL
            self._kill_orphaned_servers()

            return (False, error)

    def start_router(self, config: RouterConfig, timeout: Optional[int] = 60) -> Tuple[bool, Optional[str]]:
        """
        Start the llama-server in router mode for multi-model support.

        Args:
            config: Router configuration
            timeout: Maximum seconds to wait for server startup (None = no timeout)

        Returns:
            Tuple of (success, error_message)
        """
        self._last_error = None

        # Check if already running with same config
        if self.is_running and self._config and self._config.config_hash() == config.config_hash():
            if self.health_check():
                print(f"[LlamaCpp] Router already running with same configuration")
                return (True, None)

        # Stop existing server if running
        if self._process is not None:
            print("[LlamaCpp] Stopping existing server for router mode...")
            self.stop()

        # Kill any orphaned servers
        self._kill_orphaned_servers()

        # Validate models directory
        if not os.path.isdir(config.models_dir):
            error = f"Models directory not found: {config.models_dir}"
            self._last_error = error
            return (False, error)

        # Determine server command
        server_cmd = "llama-server.exe" if os.name == 'nt' else "llama-server"

        # Build command
        cmd = [server_cmd] + config.to_command_args()

        print(f"[LlamaCpp] Starting router...")
        print(f"[LlamaCpp] Models directory: {config.models_dir}")
        print(f"[LlamaCpp] Max models: {config.models_max}")
        print(f"[LlamaCpp] Context size: {config.context_size}")
        print(f"[LlamaCpp] Command: {' '.join(cmd)}")

        try:
            self._status = ServerStatus.STARTING

            # Start process
            if os.name == 'nt':
                self._process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
                )
            else:
                self._process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT
                )

            # Assign to job object on Windows
            self._assign_to_job(self._process)
            self._config = config
            self._mode = ServerMode.ROUTER

            # Wait for server to be ready
            if timeout is None:
                print(f"[LlamaCpp] Waiting for router to be ready (no timeout)...")
            else:
                print(f"[LlamaCpp] Waiting for router to be ready (timeout: {timeout}s)...")

            elapsed = 0
            while True:
                time.sleep(1)
                elapsed += 1

                if self.health_check():
                    self._status = ServerStatus.RUNNING
                    print(f"[LlamaCpp] Router ready! (took {elapsed}s)")
                    return (True, None)

                # Check if process crashed
                if self._process.poll() is not None:
                    try:
                        output = self._process.stdout.read().decode('utf-8', errors='ignore')
                    except Exception:
                        output = ""

                    error = f"Router crashed during startup (exit code: {self._process.returncode})"
                    if output:
                        error += f"\n\nServer output:\n{output[:2000]}"

                    self._status = ServerStatus.ERROR
                    self._last_error = error
                    self._process = None
                    self._config = None
                    self._mode = ServerMode.SINGLE_MODEL
                    return (False, error)

                if elapsed % 10 == 0:
                    print(f"[LlamaCpp] Still waiting... ({elapsed}s)")

                # Check timeout (if set)
                if timeout is not None and elapsed >= timeout:
                    error = f"Router did not start within {timeout} seconds"
                    self._last_error = error
                    self.stop()
                    return (False, error)

        except FileNotFoundError:
            error = "llama-server not found. Please install llama.cpp and add to PATH.\n" \
                   "Installation: https://github.com/ggml-org/llama.cpp/blob/master/docs/install.md"
            self._status = ServerStatus.ERROR
            self._last_error = error
            return (False, error)

        except Exception as e:
            error = f"Failed to start router: {e}"
            self._status = ServerStatus.ERROR
            self._last_error = error
            return (False, error)

    def list_models(self) -> Tuple[bool, Optional[List[Dict[str, Any]]], Optional[str]]:
        """
        List available models from the server.

        Returns:
            Tuple of (success, models_list, error_message)
        """
        if not self.is_running:
            return (False, None, "Server not running")

        try:
            import requests
            response = requests.get(f"{self.server_url}/models", timeout=10)
            response.raise_for_status()
            data = response.json()

            # Handle both /models and /v1/models response formats
            if "data" in data:
                models = data["data"]
            else:
                models = data if isinstance(data, list) else []

            return (True, models, None)

        except requests.exceptions.RequestException as e:
            return (False, None, f"Failed to list models: {e}")
        except Exception as e:
            return (False, None, f"Error: {e}")

    def load_model(self, model_name: str) -> Tuple[bool, Optional[str]]:
        """
        Load a model in router mode.

        Args:
            model_name: Name of the model to load

        Returns:
            Tuple of (success, error_message)
        """
        if not self.is_running:
            return (False, "Server not running")

        if not self.is_router_mode:
            return (False, "Server not in router mode")

        try:
            import requests
            response = requests.post(
                f"{self.server_url}/models/load",
                json={"model": model_name},
                timeout=300  # Loading can take a while
            )
            response.raise_for_status()
            print(f"[LlamaCpp] Model loaded: {model_name}")
            return (True, None)

        except requests.exceptions.RequestException as e:
            error = f"Failed to load model: {e}"
            return (False, error)
        except Exception as e:
            return (False, f"Error: {e}")

    def unload_model(self, model_name: str) -> Tuple[bool, Optional[str]]:
        """
        Unload a model in router mode.

        Args:
            model_name: Name of the model to unload

        Returns:
            Tuple of (success, error_message)
        """
        if not self.is_running:
            return (False, "Server not running")

        if not self.is_router_mode:
            return (False, "Server not in router mode")

        try:
            import requests
            response = requests.post(
                f"{self.server_url}/models/unload",
                json={"model": model_name},
                timeout=30
            )
            response.raise_for_status()
            print(f"[LlamaCpp] Model unloaded: {model_name}")
            return (True, None)

        except requests.exceptions.RequestException as e:
            error = f"Failed to unload model: {e}"
            return (False, error)
        except Exception as e:
            return (False, f"Error: {e}")

    def get_status_info(self) -> Dict[str, Any]:
        """Get detailed status information"""
        info = {
            "status": self.status.value,
            "is_running": self.is_running,
            "mode": self._mode.value,
            "server_url": self.server_url if self.is_running else None,
            "last_error": self._last_error,
        }

        if self._config and self.is_running:
            if self._mode == ServerMode.ROUTER:
                info["config"] = {
                    "models_dir": self._config.models_dir,
                    "models_max": self._config.models_max,
                    "context_size": self._config.context_size,
                    "n_gpu_layers": self._config.n_gpu_layers,
                    "port": self._config.port,
                }
            else:
                info["config"] = {
                    "model": os.path.basename(self._config.model_path),
                    "context_size": self._config.context_size,
                    "n_gpu_layers": self._config.n_gpu_layers,
                    "port": self._config.port,
                }

        if self._process:
            info["pid"] = self._process.pid

        return info


# Global singleton instance
_server_manager: Optional[LlamaCppServerManager] = None

def get_server_manager() -> LlamaCppServerManager:
    """Get the global server manager instance"""
    global _server_manager
    if _server_manager is None:
        _server_manager = LlamaCppServerManager()
    return _server_manager
