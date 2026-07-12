# Start Here

This is the shortest path from installation to a local text response. It uses an
external `llama-server` process that this node pack can own and release safely.
No cloud service or API key is required.

## 1. Install the node pack

Install `comfyui-llamacpp` from the Comfy Registry, then restart ComfyUI:

```bash
comfy node install comfyui-llamacpp
```

Stable 0.3 startup reports 17 registered llama.cpp nodes. The post-0.3 `dev`
line reports 19 because **llama.cpp Generate** and **llama.cpp Task Profile** are
additive.

## 2. Make `llama-server` available

Install a current llama.cpp server build for your CPU or GPU backend. The node
pack resolves the executable in this order:

1. The node's `binary_path` value.
2. `LLAMA_SERVER_BINARY`.
3. The compatibility variable `LLAMA_CPP_SERVER`.
4. `llama-server` or `llama-server.exe` on `PATH`.

An explicit path is the clearest choice when several builds are installed.

## 3. Add a GGUF model

Place at least one model in:

```text
ComfyUI/models/LLM/gguf/
```

Configured `LLM` or `llm` roots from `extra_model_paths.yaml` are also supported.
Restart ComfyUI after changing model-root configuration.

For a vision model, keep the matching projector separate and select it explicitly.
A projector filename does not prove that it is compatible with a model.

## 4. Run Setup Check

Open **Workflow > Browse Templates**, choose this node pack, and load **Setup
Check**. Queue it once.

The existing **llama.cpp Server Status** node reports `Setup: ready` when it can
resolve and probe `llama-server` and can discover at least one model. It also
shows bounded device, model-root, model, and projector information. `Setup: needs
attention` is followed by an actionable warning.

If your binary is not on ComfyUI's environment path, enter its full path in the
Status node and run the check again.

## 5. Generate text

Load **Quick Text** from the same template browser:

1. Select an installed GGUF on **Start llama.cpp Server**.
2. Set `llama-server Binary` only if Setup Check needed an explicit path.
3. Edit the visible prompt if desired.
4. Queue the workflow.

The three-node workflow starts one owned server, sends the prompt, and displays
the response. Use **Show Advanced** only when the defaults do not fit the model or
machine.

For a new post-0.3 workflow, load **Canonical Text** or replace Basic Prompt and
Prompt Output with one **llama.cpp Generate** node. Convert Generate's advanced
`Server URL` widget to an input before connecting the Start node URL, or leave it
empty to use the currently owned runtime. Generate provides its own live preview
and terminal output, so a Prompt Output node is optional.

## 6. Release GPU memory

Use either of these after generation:

- ComfyUI's native **Unload Models** action, which also asks this pack to release
  its owned llama.cpp runtime.
- **Release llama.cpp VRAM** or **Stop llama.cpp Server** for an explicit graph
  control.

An attached server that this pack did not start is never stopped by implicit
unload behavior.

## Next steps

- [README](../README.md) for every node and prompt option.
- [Canonical Generate](canonical-generate.md) for the recommended post-0.3 node,
  task profiles, live Stop, passive discovery, and terminal release.
- [Lifecycle and VRAM ownership](lifecycle.md) for release guarantees.
- [Troubleshooting](troubleshooting.md) for setup and runtime failures.
- [Example workflows](../example_workflows/) for router, vision, structured
  output, and GPU handoff graphs.
