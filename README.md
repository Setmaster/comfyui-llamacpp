# ComfyUI LlamaCpp Suite

A modular llama.cpp integration for ComfyUI, providing clean and extensible nodes for local LLM inference.

## Features

- **Single Model Mode** - Launch llama-server with a specific model
- **Router Mode** - Multi-model support with dynamic loading/unloading (LRU eviction)
- **Basic Prompt** - Send prompts with full sampling control and thinking mode support
- **Model Management** - List, load, and unload models in router mode

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

### Start LlamaCpp Server (Single Model)

Starts the llama-server with a specific model. Best for workflows that use one model.

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

### Start LlamaCpp Router (Multi-Model)

Starts the llama-server in router mode for dynamic multi-model support. Models are loaded on-demand and managed with LRU eviction.

**Inputs:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| context_size | int | 4096 | Context window size (applied to all models) |
| gpu_layers | string | empty | Layers to offload to GPU. Empty = all layers |
| main_gpu | int | 0 | Primary GPU index |
| models_max | int | 4 | Maximum models loaded simultaneously |
| port | int | 8080 | Server port |
| threads | string | empty | CPU threads. Empty = auto |
| batch_size | int | 512 | Prompt processing batch size |
| flash_attention | bool | false | Enable flash attention |
| models_autoload | bool | true | Auto-load models on first request |
| timeout | string | 60 | Startup timeout in seconds. Empty = no timeout |

**Outputs:**
| Name | Type | Description |
|------|------|-------------|
| server_url | STRING | Server URL |
| success | BOOLEAN | True if router started successfully |

### Stop LlamaCpp Server

Stops the running server (works for both single model and router mode).

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
| info | STRING | Detailed status information (includes mode) |

### LlamaCpp Basic Prompt

Sends a prompt to the llama-server and returns the response. Supports thinking/reasoning models.

**Inputs:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| prompt | string | - | The user prompt to send to the LLM |
| model | string | empty | Model to use (router mode only). Empty = use loaded model |
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

### LlamaCpp List Models

Lists available models from the server. In router mode, shows load status.

**Outputs:**
| Name | Type | Description |
|------|------|-------------|
| models_json | STRING | Full model info as JSON |
| models_list | STRING | Simple list of model names with status |

### LlamaCpp Load Model (Router Mode)

Explicitly loads a model into memory.

**Inputs:**
| Parameter | Type | Description |
|-----------|------|-------------|
| model_name | string | Name of the model to load |

**Outputs:**
| Name | Type | Description |
|------|------|-------------|
| success | BOOLEAN | True if loaded successfully |
| message | STRING | Status message |

### LlamaCpp Unload Model (Router Mode)

Unloads a model to free VRAM.

**Inputs:**
| Parameter | Type | Description |
|-----------|------|-------------|
| model_name | string | Name of the model to unload |

**Outputs:**
| Name | Type | Description |
|------|------|-------------|
| success | BOOLEAN | True if unloaded successfully |
| message | STRING | Status message |

## Usage Examples

### Single Model Workflow
```
[Start LlamaCpp Server] → [Basic Prompt] → [Output]
         ↓
    (model.gguf)
```

### Multi-Model Workflow (Router Mode)
```
[Start LlamaCpp Router] → [Basic Prompt (model=small.gguf)] → [Classifier Output]
                        → [Basic Prompt (model=large.gguf)] → [Generation Output]
```

### Explicit Model Control
```
[Start LlamaCpp Router] → [Load Model] → [Basic Prompt] → [Unload Model]
```

## Recommended Models

### Instruct Models (for prompt enhancement)
- [Qwen3-4B-GGUF](https://huggingface.co/Qwen/Qwen3-4B-GGUF)
- [Llama-3.2-3B-Instruct-GGUF](https://huggingface.co/bartowski/Llama-3.2-3B-Instruct-GGUF)

### Thinking/Reasoning Models
- [Qwen3-4B-Thinking-GGUF](https://huggingface.co/unsloth/Qwen3-4B-Thinking-2507-GGUF)

## Roadmap

- [x] Basic Prompt - Send prompts to the server
- [x] Router Mode - Multi-model support with dynamic loading
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

### Router mode not working
Router mode requires a recent version of llama.cpp with router support. Update to the latest version if you encounter issues.

## License

MIT License
