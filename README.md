# ComfyUI LlamaCpp Suite

A modular llama.cpp integration for ComfyUI, providing clean and extensible nodes for local LLM inference.

## Features

- **Start LlamaCpp Server** - Launch llama-server with configurable parameters
- **Stop LlamaCpp Server** - Cleanly shutdown the server
- **Server Status** - Monitor server state for workflow conditionals
- **Basic Prompt** - Send prompts to the server with full sampling control

### Key Design Principles

- **Singleton Server Management** - Only one llama-server instance runs at a time
- **Smart Restart** - Server only restarts when configuration changes
- **Clean Shutdown** - Automatic cleanup on ComfyUI exit (Windows job objects, signal handlers)
- **Orphan Cleanup** - Kills any stray llama-server processes
- **Thinking Mode Support** - Capture reasoning content from thinking models separately

## Installation

### 1. Clone the Repository

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/Setmaster/comfyui-llamacpp
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
| gpu_layers | string | empty | Layers to offload to GPU. Empty = all layers, 0 = CPU only |
| main_gpu | int | 0 | Primary GPU index |
| port | int | 8080 | Server port |
| threads | string | empty | CPU threads. Empty = auto |
| batch_size | int | 512 | Prompt processing batch size |
| flash_attention | bool | false | Enable flash attention |
| timeout | string | 60 | Startup timeout in seconds. Empty = no timeout |

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

### LlamaCpp Basic Prompt

Sends a prompt to the llama-server and returns the response. Supports thinking/reasoning models.

**Inputs:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| prompt | string | - | The user prompt to send to the LLM |
| server_url | string | empty | Server URL. Empty = use running server |
| system_prompt | string | empty | Optional system prompt |
| enable_thinking | bool | true | Enable thinking mode for supported models |
| max_tokens | int | 2048 | Maximum tokens to generate |
| temperature | float | 0.7 | Sampling temperature (0.0-2.0) |
| top_p | float | 0.9 | Top-p nucleus sampling (0.0-1.0) |
| top_k | int | 40 | Top-k sampling (0 = disabled) |
| min_p | float | 0.05 | Min-p sampling threshold |
| repeat_penalty | float | 1.1 | Repetition penalty (1.0 = none) |
| seed | int | 0 | Random seed for generation |

**Outputs:**
| Name | Type | Description |
|------|------|-------------|
| response | STRING | The generated response text |
| thinking | STRING | Reasoning/thinking content (for supported models) |

## Recommended Models

### Instruct Models (for prompt enhancement)
- [Qwen3-4B-GGUF](https://huggingface.co/Qwen/Qwen3-4B-GGUF)
- [Llama-3.2-3B-Instruct-GGUF](https://huggingface.co/bartowski/Llama-3.2-3B-Instruct-GGUF)

### Thinking/Reasoning Models
- [Qwen3-4B-Thinking-GGUF](https://huggingface.co/unsloth/Qwen3-4B-Thinking-2507-GGUF)

## Roadmap

Future nodes planned:
- [x] Basic Prompt - Send prompts to the server
- [ ] Router Mode - Multi-model support with dynamic loading
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
- Try reducing `gpu_layers` to offload some layers to CPU
- Check llama-server output in console for specific errors

### No thinking output
The `thinking` output only contains content for models that support reasoning mode (like Qwen3-Thinking, DeepSeek-R1). Standard instruct models won't produce thinking output.

## License

MIT License
