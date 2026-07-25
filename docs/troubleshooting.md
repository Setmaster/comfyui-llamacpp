# Troubleshooting

Start with **llama.cpp Server Status**. While idle, it reports setup readiness,
resolved binary and version, bounded device information, configured model roots,
model and projector counts, and actionable warnings without starting a server.
While running, it also reports mode, lifecycle state, owned PID or process group,
Windows Job assignment, supported capabilities, pending release, active
generation count, the last error, and a bounded redacted server-log tail.

## `llama-server` was not found

Resolution order is the node's `binary_path`, the `LLAMA_SERVER_BINARY`
environment variable, the compatibility variable `LLAMA_CPP_SERVER`, then
`PATH`.

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

Canonical Generate checks current router launch or preset target metadata when
the router supplies it. A local GGUF is resolved under the active router root and
compared to the complete normalized target; a matching basename or suffix in
another directory is not accepted. If the directory ID actually targets a
different file, the node raises `[model_missing]` before generation. Malformed
or conflicting evidence is rejected without printing the router's local path.
An exact canonical live router ID remains authoritative when no local file has
that name. Routers that omit target evidence retain the older ID-only fallback,
so this check does not make multi-quantization bundles portable.

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

## Generate raises instead of returning an error string

This is the canonical node's intended contract. Authentication, TLS, missing
model, timeout, transport, protocol, upstream, invalid request, structured JSON,
and terminal release failures raise a categorized redacted exception. Downstream
nodes do not execute on those failures.

Partial text also raises by default. Select `return_marked_partial` only when the
downstream graph is explicitly prepared to inspect the typed result's state and
error. A cancelled result is not silently relabelled complete because some text
arrived first.

## Remote credential destination is not approved

For authenticated non-loopback Canonical Generate connections, configure an
exact server-side binding before starting ComfyUI. The origin includes the
scheme and port, and the value is the API-key environment name selected by the
Connection or Generate node:

```bash
export LLAMACPP_REMOTE_AUTH_BINDINGS='{"https://llm.example.test:443":"LLAMACPP_API_KEY_LAN"}'
```

Also keep TLS certificate verification enabled. A different hostname, scheme,
port, key name, malformed JSON value, or disabled verification is rejected
before any request is sent. Loopback credentials do not require this binding.

## Stop generation is unavailable

Generate labels the exact action **Stop generation** only after a positive probe
of llama.cpp's resumable-stream interface. A 404 or 405 means unsupported. A
timeout, transport failure, or malformed success is Unknown. Those cases use
**Stop Comfy job**, which requests prompt-targeted Comfy interruption and does
not claim node-local scope.

If final status says stream cleanup was unconfirmed, the backend attempted the
exact DELETE but did not receive positive upstream confirmation. Inspect the
local network, authentication, reverse proxy, and llama.cpp build. The node does
not escalate that failure into a broader workflow or runtime stop.

## Managed model Refresh shows Unknown or Missing

Refresh is intentionally passive. It calls router `/models` without reload and
selected-model `/props` with `autoload=false`. An unloaded model may therefore
have Unknown context or modality facts. Unknown means no authoritative passive
evidence was available; it does not mean unsupported.

Missing means the saved exact value was absent from the current managed runtime
response. The value remains selectable so the workflow is not silently changed.
Use **llama.cpp List Models** to inspect authoritative router IDs, then choose a
replacement explicitly. Browser Refresh never probes an arbitrary attached URL.

## Task Profile is Missing or Changed

The saved workflow snapshot remains authoritative in both states:

- Missing means the current Comfy user's local file has no profile with that ID.
- Changed means that local entry has different content from the saved snapshot.

Select a local entry and press **Update Saved Snapshot** to copy it explicitly.
Selection or Refresh alone never mutates the workflow. If the local profile file
is invalid, fix `comfyui-llamacpp/profiles.json` in the current Comfy user data
directory. The backend rejects oversized documents, unknown or duplicate keys,
duplicate IDs, invalid UTF-8 or JSON, and attempts to override built-in Freeform.

## App Mode does not show terminal text in the central result pane

The tested frontend 1.45.20 retains terminal text in Comfy's native jobs and
history output but does not render that inline text in App Mode's central result
pane. The canonical workflow therefore exposes transient read-only **Generation
Status** and **Live Response** fields. They are current-session feedback, reset
on reload, and are not serialized or surrogate output files.

## Release After Generation fails

Attached endpoints are rejected before prompt submission. For an owned router,
the node also requires one exact model ID. Scoped router cleanup never stops the
router as fallback, so ambiguous, mismatched, or nonterminal state is reported as
a release failure while the router and unrelated models remain intact.

If the caller times out while waiting, accepted cleanup continues in the
lifecycle coordinator. Check Server Status before submitting new work for the
same direct runtime or exact router model.

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

- Leave direct-mode **Vision Projector** on `(auto)` when the compatible
  projector is installed. Server Status shows the resolved projector and mode.
- If Start reports several distinct compatible projector identities, select one
  exact file. Strong equivalent projector matches across configured roots are
  ranked Q8_0, BF16, F16, then F32, even when a lower-ranked copy is adjacent.
  If Start reports no compatible projector for a known VLM, install the
  matching `mmproj`, select one exact file, or use `(none - text only)`
  deliberately.
- If an image is connected while the running model reports no vision support,
  Generate stops before submission with a capability error instead of silently
  omitting the image. Selecting `(none - text only)` produces a specific
  corrective message.
- In router mode, keep the model and matching projector in one dedicated
  subdirectory.
- Confirm the model's `/props` modalities include image input.
- Test one image before enabling `include_image_batch`.
- Confirm `image_amount` includes the connected socket.

Legacy prompt nodes return an image conversion failure with `success=false`;
Generate raises an invalid-request error. Neither path silently skips the image.

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
