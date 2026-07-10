# Troubleshooting

Start with **llama.cpp Server Status**. It reports mode, lifecycle state, owned
PID or process group, Windows Job assignment, binary identity, supported
capabilities, pending release, active generation count, the last error, and a
bounded redacted server-log tail.

## `llama-server` was not found

Resolution order is the start node's `binary_path`, the
`LLAMA_SERVER_BINARY` environment variable, then `PATH`.

Verify with the same environment that starts ComfyUI:

```bash
llama-server --version
llama-server --help
```

On Windows, use the full path to `llama-server.exe` when ComfyUI was started
from a launcher that has a different `PATH`.

## A requested option is unsupported

The pack probes each binary and rejects capability-gated options before spawn.
Update llama.cpp or return the option to its compatibility default. In
particular, router mode, idle sleep, symbolic GPU-layer values, and some modern
flags have changed across llama.cpp builds.

Do not duplicate typed transport, authentication, model, or lifecycle options
inside `extra_args`. The pack rejects overrides that would make its readiness
URL or ownership model disagree with the actual process.

If an upgraded workflow now reports that `extra_args` cannot override a typed
option, remove that duplicate raw flag and set the corresponding node widget
instead. Numeric GPU layers are the compatibility choice when an older binary
does not advertise `auto` or `all`.

## Port already in use

The manager refuses to adopt an unknown listener. Change `port`, stop the
listener yourself if you own it, or attach to it explicitly through a
connection profile. It will not kill a process simply because it is named
`llama-server`.

## Server starts and exits

Common causes are:

- Model architecture unsupported by that llama.cpp build.
- Insufficient VRAM or RAM for weights, context, and KV cache.
- An incompatible VLM projector.
- GPU backend DLLs or shared libraries not colocated with the executable.
- Invalid advanced arguments.

Read the bounded log tail in Server Status. Reduce `context_size`, use a smaller
quantization, reduce numeric GPU layers, or validate the model with the same
`llama-server` from a terminal.

## Model dropdown is empty

Place models under `ComfyUI/models/LLM/gguf`, or configure an `LLM` root in
`extra_model_paths.yaml`. If the configured root is `F:/models/LLM`, models may
live under `F:/models/LLM/gguf`.

Restart ComfyUI after changing model-root configuration. Projector filenames
must contain `mmproj` to appear in the separate projector list.

## Router model is not found or is ambiguous

Use **llama.cpp List Models** to inspect exact router IDs and aliases. A local
filename is resolved only when it maps unambiguously to one record. Put each
VLM bundle in its own directory and avoid colliding directory names, filenames,
or aliases.

The native router exposes one configured local root plus its own cache. It sees
root-level GGUFs and one immediate bundle level only. Put one base model or one
complete shard set, plus at most one projector, in each bundle. Multiple
quantizations in one directory are filesystem-order-dependent upstream; deeper
directories are not visible. A model appearing in the recursive direct-mode
dropdown therefore does not prove it exists in the active router catalog.

Load and unload operations can take time. Their `operation_timeout` is an
overall deadline, not a socket-read timeout per poll.

Catalog reload is serialized with generation, load, unload, release, and
replacement. It compares model identities and launch options, not GGUF content
at an unchanged path. Stop and reload a running child after replacing a file in
place.

## Native Unload Models did not release the LLM

Check:

1. The runtime status says `owned: true`.
2. Comfy sent `POST /free` or `POST /api/free` with `unload_models` or
   `free_memory` true.
3. `release_pending` is not waiting for an active generation.
4. Router status does not contain a still-loading model.
5. The response header and `llamacpp.lifecycle` event show the release result.

An attached endpoint is intentionally untouched. Use that runtime's own
control surface if you started it outside this pack.

## Release is deferred

This is expected when managed generation is active. The final generation lease
performs the pending release. If the generation is hung, use Comfy's interrupt
control so the request can unwind and close its HTTP response.

## Windows reports descendant fallback

`descendant_fallback: true` after launch means Job Object assignment failed.
Ordinary explicit stop still tracks validated descendants, but abrupt Comfy
termination is not proven safe. Inspect the local warning, remove process/job
restrictions, and repeat the acceptance test. A passing Windows crash-cleanup
test requires `windows_job_assigned: true`.

## Linux status PID is Python

On Linux, Server Status reports the small Python supervisor that owns the
unique process group. `llama-server` runs as its child. This is expected and is
what lets a hard Comfy owner exit trigger kernel-backed group cleanup. Process
commands remain reported as the original redacted llama-server command.

## A VLM fails or ignores the image

- Select the matching direct-mode projector explicitly.
- In router mode, keep the model and matching projector in one dedicated
  subdirectory.
- Confirm the model's `/props` modalities include image input.
- Test one image before enabling `include_image_batch`.
- Confirm `image_amount` includes the connected socket.

An image conversion failure is returned through the prompt node with
`success=false`; it is no longer silently skipped.

## Structured output fails

First test `json_object`, then a small JSON Schema. The node validates that
schema text parses to an object, but model and llama.cpp support still determine
whether strict generation succeeds. GBNF mode requires a nonempty grammar.

## Thinking output is empty

Only compatible models and chat templates emit reasoning content. A normal
instruct model may return a response with an empty `thinking` output. Check the
model's llama.cpp chat-template behavior before treating this as a node error.

## VRAM does not immediately match baseline

Process or router terminal state proves ownership release, not an instantaneous
driver accounting update. Sample memory for several seconds and run the next
GPU workload. If functional allocation still fails, capture:

- Server Status JSON and log tail.
- `nvidia-smi` before start, after generation, after release, and after a short
  convergence window.
- Whether the runtime was direct, router, or attached.
- Whether Comfy pre-eviction was enabled.
- The exact llama.cpp version and model quantization.
