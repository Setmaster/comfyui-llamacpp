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

On Windows, deploy the complete matching release set into a clean directory.
Current releases can require companion files such as
`llama-server-impl.dll`, `llama-common.dll`, `ggml-cuda.dll`, and the separate
CUDA runtime archive. Replacing only the executable, or overlaying a new build
onto an older directory, can leave an incompatible mixture.

## Roll back the pinned Windows runtime

The plugin checkout and llama.cpp binary are separate rollback surfaces. To
return the maintainer workstation from b9957 to the preserved b8261 runtime:

1. Finish or interrupt every workflow, stop the owned llama.cpp runtime, and
   exit ComfyUI.
2. Confirm no `llama-server.exe` process remains and no Comfy Python command
   is still running `main.py`.
3. Preserve b9957 by renaming the complete directory, then rename the complete
   b8261 rollback directory into the stable `C:\llama` path.

```powershell
$ErrorActionPreference = "Stop"
$active = "C:\llama"
$rollback = "C:\llama-b8261-e22cd0aa1-rollback-20260711"
$standby = "C:\llama-b9957-c4ae9a88f-standby"
$activeHash = "20b7b426afaa175e3374e16f2e99b2ecb1d63a2784c4a45e5c58141b0e6ff6ab"
$rollbackHash = "2ede8d4d32308a8e625f804e7a39e9e78f2031495617ae6ae6432116a335bc44"

$busy = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq "llama-server.exe" -or
    (($_.Name -eq "python.exe" -or $_.Name -eq "pythonw.exe") -and
     $_.CommandLine -match "(^|\s)main\.py(\s|$)")
}
if ($busy) { throw "Stop ComfyUI and llama-server before rollback" }

if (-not (Test-Path -LiteralPath $active -PathType Container)) {
    throw "Active b9957 directory is missing: $active"
}
if (-not (Test-Path -LiteralPath $rollback -PathType Container)) {
    throw "Rollback b8261 directory is missing: $rollback"
}
if (Test-Path -LiteralPath $standby) {
    throw "Refusing to overwrite existing standby directory: $standby"
}
if ((Get-FileHash "$active\llama-server.exe" -Algorithm SHA256).Hash -ne $activeHash) {
    throw "Active b9957 executable hash does not match the pinned build"
}
if ((Get-FileHash "$rollback\llama-server.exe" -Algorithm SHA256).Hash -ne $rollbackHash) {
    throw "Rollback b8261 executable hash does not match the preserved build"
}

Rename-Item -LiteralPath $active -NewName (Split-Path -Leaf $standby)
$rollbackMoved = $false
try {
    Rename-Item -LiteralPath $rollback -NewName (Split-Path -Leaf $active)
    $rollbackMoved = $true

    $version = & "$active\llama-server.exe" --version
    if ($LASTEXITCODE -ne 0) { throw "b8261 version check failed" }
    $devices = & "$active\llama-server.exe" --list-devices
    if ($LASTEXITCODE -ne 0) { throw "b8261 device check failed" }
    $version
    $devices
} catch {
    if ($rollbackMoved -and (Test-Path -LiteralPath $active) -and
        -not (Test-Path -LiteralPath $rollback)) {
        Rename-Item -LiteralPath $active -NewName (Split-Path -Leaf $rollback)
    }
    if ((Test-Path -LiteralPath $standby) -and
        -not (Test-Path -LiteralPath $active)) {
        Rename-Item -LiteralPath $standby -NewName (Split-Path -Leaf $active)
    }
    throw
}
```

The expected rollback build is `8261 (e22cd0aa1)`. Its executable SHA-256 is
`2ede8d4d32308a8e625f804e7a39e9e78f2031495617ae6ae6432116a335bc44`.
Restart ComfyUI only after those checks pass.

b8261 is an emergency direct-compatibility rollback. It passed the 0.3 direct
smoke, but it predates the current router model-management APIs and fixes used
by the full 0.3 feature set. For a fully matched 0.2.1 stack, also switch the
plugin checkout to the `0.2.1` tag using the
[migration rollback](migration-0.3.md#rollback).

To restore b9957 later, stop ComfyUI and every llama-server process again,
rename the active b8261 directory back to
`llama-b8261-e22cd0aa1-rollback-20260711`, and rename
`llama-b9957-c4ae9a88f-standby` to `llama`. Verify build
`9957 (c4ae9a88f)`, executable SHA-256
`20b7b426afaa175e3374e16f2e99b2ecb1d63a2784c4a45e5c58141b0e6ff6ab`,
and CUDA device discovery before restarting ComfyUI. Never overlay the two
directories or delete either generation before acceptance.

## Non-ASCII output is corrupted

If text such as `café` becomes `cafÃ©`, the SSE stream was decoded with the
wrong inferred charset. Version 0.3.0 and later read raw SSE bytes and decode
them explicitly as UTF-8. Confirm the installed checkout is `0.3.0` or later,
restart ComfyUI, and repeat with a short exact Unicode response.

If corruption remains, call the same server once with non-streaming output. A
correct non-streaming response plus a corrupted workflow response points to
the client path. Corruption in both responses points to the model, prompt
template, proxy, or server build.

## RTX 50-series diagnostic overrides

llama.cpp b9957 enables newer Blackwell paths by default. If a workload has a
repeatable CUDA correctness failure, try one upstream diagnostic override at a
time before starting ComfyUI:

```bat
set GGML_CUDA_PDL=0
```

or:

```bat
set LLAMA_ATTN_ROT_DISABLE=1
```

Those `set` commands affect only that Command Prompt. Launch ComfyUI from the
same window so its llama-server child inherits the variable. In PowerShell,
use one of the equivalent session-scoped forms before launching ComfyUI:

```powershell
$env:GGML_CUDA_PDL = "0"
# or
$env:LLAMA_ATTN_ROT_DISABLE = "1"
```

The first disables CUDA Programmatic Dependent Launch. The second disables the
attention rotation used with supported quantized KV-cache paths. These are
diagnostic environment variables, not recommended defaults or node settings.
Remove the override after isolating the problem, and record the exact model,
quantization, context, KV-cache type, and llama.cpp build.

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

## Linux reports that pidfd support is required

Linux lifecycle ownership requires both `pidfd_open` and
`waitid(P_PIDFD)`. Those interfaces are normally available on Linux 5.4 or
newer, although a distribution can backport them. The pack probes the actual
interfaces before spawning a server, so this error does not leave a new
llama.cpp process behind.

Upgrade the host kernel, or run ComfyUI on Windows or another supported POSIX
host. Do not bypass this check with numeric PID cleanup. The stable pidfd is
what lets the pack reject PID reuse and consume only the process generation it
launched.

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
