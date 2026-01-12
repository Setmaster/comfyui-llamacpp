# ComfyUI LlamaCpp Suite

A modular llama.cpp integration for ComfyUI, providing clean and extensible nodes for local LLM inference.

## Features

- **Start LlamaCpp Server** - Launch llama-server with configurable parameters
- **Stop LlamaCpp Server** - Cleanly shutdown the server
- **Server Status** - Monitor server state for workflow conditionals

### Key Design Principles

- **Singleton Server Management** - Only one llama-server instance runs at a time
- **Smart Restart** - Server only restarts when configuration changes
- **Clean Shutdown** - Automatic cleanup on ComfyUI exit (Windows job objects, signal handlers)
- **Orphan Cleanup** - Kills any stray llama-server processes

## Installation

### 1. Clone the Repository

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/YOUR_USERNAME/comfyui-llamacpp
```

### 2. Install Python Dependencies

```bash
cd comfyui-llamacpp

# Windows (portable ComfyUI)
..\..\..\python_embeded\python.exe -s -m pip install -r requirements.txt

# Linux/Mac
pip install -r requirements.txt
```

### 3. Install llama.cpp

The nodes require `llama-server` to be available in your PATH.

**Windows:**
```bash
winget install llama.cpp
```

**Linux/Mac:**
See [llama.cpp installation guide](https://github.com/ggml-org/llama.cpp/blob/master/docs/install.md)

### 4. Add Models

Place your `.gguf` model files in:
```
ComfyUI/models/LLM/gguf/
```

The directory will be created automatically on first run if it doesn't exist.

## Nodes

### Start LlamaCpp Server

Starts the llama-server with your chosen configuration. If the server is already running with the same settings, it will be reused.

**Inputs:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| model | dropdown | - | Select from available .gguf models |
| context_size | int | 4096 | Context window size (tokens) |
| n_gpu_layers | int | 999 | Layers to offload to GPU (999 = all) |
| main_gpu | int | 0 | Primary GPU index |
| port | int | 8080 | Server port |
| threads | int | 0 | CPU threads (0 = auto) |
| batch_size | int | 512 | Prompt processing batch size |
| flash_attention | bool | false | Enable flash attention |
| timeout | int | 60 | Startup timeout (seconds) |

**Outputs:**
| Name | Type | Description |
|------|------|-------------|
| server_url | STRING | Server URL (e.g., "http://127.0.0.1:8080") |
| success | BOOLEAN | True if server started successfully |

### Stop LlamaCpp Server

Stops the running server. Safe to call even if no server is running.

**Inputs:**
| Parameter | Type | Description |
|-----------|------|-------------|
| trigger | * | Optional workflow trigger input |

**Outputs:**
| Name | Type | Description |
|------|------|-------------|
| success | BOOLEAN | True if stopped (or wasn't running) |
| message | STRING | Status message |

### LlamaCpp Server Status

Returns current server status information.

**Outputs:**
| Name | Type | Description |
|------|------|-------------|
| is_running | BOOLEAN | True if server is running |
| status | STRING | Status state (stopped/starting/running/error) |
| info | STRING | Detailed status information |

## Recommended Models

### Instruct Models (for prompt enhancement)
- [Qwen3-4B-GGUF](https://huggingface.co/Qwen/Qwen3-4B-GGUF)
- [Llama-3.2-3B-Instruct-GGUF](https://huggingface.co/bartowski/Llama-3.2-3B-Instruct-GGUF)

### Thinking/Reasoning Models
- [Qwen3-4B-Thinking-GGUF](https://huggingface.co/unsloth/Qwen3-4B-Thinking-2507-GGUF)

## Roadmap

Future nodes planned:
- [ ] Chat Completion - Send messages to the server
- [ ] Text Embedding - Generate embeddings for text
- [ ] VLM Support - Vision-language model integration
- [ ] Model Info - Display model metadata

## Troubleshooting

### "llama-server not found"
Ensure llama.cpp is installed and `llama-server` is in your system PATH.

### Server won't start
- Check if another process is using the port (default 8080)
- Verify the model file isn't corrupted
- Check you have enough VRAM for the model

### Server crashes on startup
- Try reducing `context_size`
- Try reducing `n_gpu_layers` to offload some layers to CPU
- Check llama-server output in console for specific errors

## License

MIT License
