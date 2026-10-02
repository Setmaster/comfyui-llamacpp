# Start Here

This is the shortest path from installation to a local text response. It uses an
external `llama-server` process that this node pack can own and release safely.
No cloud service or API key is required.

## 1. Install the node pack

Install `comfyui-llamacpp` from the Comfy Registry, then restart ComfyUI:

```bash
comfy node install comfyui-llamacpp
```

Stable 0.3 startup reports 17 registered llama.cpp nodes. Follow the stable
steps below for that installation. Setup Check, Quick Text, Generate, task
profiles, and automatic local projector matching require the unreleased 0.4
candidate. To test it deliberately, use the [candidate source installation
instructions](../README.md#test-the-unreleased-04-candidate), restart ComfyUI,
and confirm startup reports `0.4.0` and 19 nodes. Candidate testing does not
replace the maintainer's hands-on acceptance gate before release.

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

For a vision model, install its matching `mmproj` GGUF under a configured model
root. On stable 0.3, select that exact file in the Start node's `mmproj` field;
`(auto)` does not find a matching local projector in this release.

On the 0.4 candidate, **Vision Projector** can remain on `(auto)` to select one
match only when local GGUF metadata proves compatibility. Folder adjacency and
filenames alone do not prove a match. You can also choose one exact projector
file, or select `(none - text only)` to disable vision.

## 4. Generate text on stable 0.3

Build this small graph:

1. Add **Start llama.cpp Server** and select an installed GGUF.
2. Set its `binary_path` if the executable is not on ComfyUI's environment path.
3. Connect its `server_url` output to **llama.cpp Basic Prompt**.
4. Connect Basic Prompt's `response` to **llama.cpp Prompt Output**.
5. Enter a prompt and queue the workflow.

The graph starts one owned server and displays the response. For a connected
example, open `examples/direct-text.json` from the installed custom-node
directory and replace its model selection with your local model. Stable 0.3
ships five examples in that directory. It does not include Setup Check, Quick
Text, or the candidate's workflow-template directory.

## 5. Candidate only: Setup Check and templates

Open **Workflow > Browse Templates**, choose this node pack, and load **Setup
Check**. Queue it once.

On the candidate, **llama.cpp Server Status** reports `Setup: ready` when it can
resolve and probe `llama-server` and can discover at least one model. It also
shows bounded device, model-root, model, and projector information. `Setup: needs
attention` is followed by an actionable warning.

If your binary is not on ComfyUI's environment path, enter its full path in the
Status node and run the check again.

Then load **Quick Text** from the same template browser:

1. Select an installed GGUF on **Start llama.cpp Server**.
2. Set `llama-server Binary` only if Setup Check needed an explicit path.
3. Edit the visible prompt if desired.
4. Queue the workflow.

The three-node workflow starts one owned server, sends the prompt, and displays
the response. Use **Show Advanced** only when the defaults do not fit the model or
machine.

For a new candidate workflow, load **Canonical Text** or replace Basic Prompt and
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
- [Candidate example workflows](../example_workflows/) for router, vision,
  structured output, and GPU handoff graphs. Stable users should open their
  installed `examples/` directory instead.
