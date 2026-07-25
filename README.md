# ComfyUI llama.cpp Suite

Focused local `llama-server` integration for ComfyUI. It provides full text and
vision prompting, structured output, direct and router modes, and explicit
control over the external process that owns LLM VRAM.

Version 0.3 keeps the external-process design that motivated this project. A
model is not hidden inside ComfyUI's Python process, and this pack can release
only the `llama-server` process tree it started. ComfyUI's native **Unload
Models** action also releases this pack's owned runtime. The existing explicit
stop and unload nodes remain available.

Post-0.3 development adds one recommended **llama.cpp Generate** node instead
of another family of overlapping prompt nodes. It covers text, vision,
structured output, token bans, live preview, truthful cancellation, portable
task profiles, strict failures, and optional terminal release. All 17 nodes
from 0.3 remain registered with their released workflow contracts.

The stable release is `0.3.0`. It is available from the Comfy Registry and the
`master` branch. The additive `0.4.0` candidate remains on `dev` until it passes
the maintainer's hands-on acceptance gate. It has not been published to the
Registry.

New installation? Follow [Start Here](docs/start-here.md), then load **Setup
Check** and **Quick Text** from ComfyUI's workflow template browser.

## What it covers

- Direct mode for one GGUF model.
- Current `llama-server` router mode with exact model identities and terminal
  load/unload barriers.
- Freeform chat completions with sampling, thinking/reasoning output, stop
  sequences, token bans, and prompt-prefix caching.
- VLM requests with 0 to 10 Comfy `IMAGE` inputs and optional full-batch input.
- Metadata-backed local projector selection for known VLM families, with an
  explicit text-only mode and fail-closed ambiguity handling.
- Non-destructive, backend-applied Image2Prompt and Prompt Enhancer templates.
- JSON object, JSON Schema, and GBNF structured-output constraints.
- Token counting and live model/server properties.
- Reusable local connection profiles with API-key environment variables, TLS
  verification, and request deadlines.
- One canonical Generate surface with Default or Custom sampling, Auto or
  explicit thinking control, strict partial-output policy, bounded live preview,
  and a versioned result.
- Small user-owned task profiles that are explicitly copied into portable
  workflow snapshots. Freeform is the only bundled profile.
- Passive managed model discovery that never autoloads a router model and keeps
  missing saved selections visible.
- Optional release-after-generation that withholds outputs until exact managed
  direct or router cleanup reaches a terminal result.
- Positively owned process trees, bounded redacted logs, and deterministic stop
  barriers. Linux adds kernel-backed abrupt-owner cleanup; Windows uses Job
  Objects when available. It never sweeps processes by name.
- Two-sided GPU handoff: optionally evict Comfy-managed models before starting
  an owned LLM, then release the LLM through Comfy's native unload action.

This is intentionally not an agent, RAG, MCP, cloud-provider, or conversation
database suite. It is a small local llama.cpp runtime and generation surface.

## Requirements

- ComfyUI with Python 3.10 or newer.
- A current `llama-server` build. Optional controls are capability-checked
  before launch. Router mode requires a build that exposes `--models-dir` and
  `--models-max`.
- Linux requires working `pidfd_open` and `waitid(P_PIDFD)` support, normally a
  Linux 5.4 or newer kernel unless those interfaces were backported. The pack
  checks both capabilities before spawning `llama-server` and fails with an
  actionable error on an unsupported host.
- One or more GGUF models.
- For GPU inference, a llama.cpp build for the installed CUDA, Vulkan, ROCm,
  Metal, or other supported backend.

No cloud service or cloud API key is required.

## Installation

### Install from the Comfy Registry

The Registry ID is `comfyui-llamacpp`. Install it with the Comfy CLI:

```bash
comfy node install comfyui-llamacpp
```

The public package page is
[ComfyUI llama.cpp Suite](https://registry.comfy.org/nodes/comfyui-llamacpp).
Restart ComfyUI after installation.

### Install the stable release from source

```bash
cd ComfyUI/custom_nodes
git clone --branch master https://github.com/Setmaster/comfyui-llamacpp.git
cd comfyui-llamacpp
```

Install dependencies with the Python that launches ComfyUI:

```bash
# ComfyUI virtual environment on Windows
C:\ComfyUI\venv\Scripts\python.exe -m pip install -r requirements.txt

# ComfyUI portable Windows build, adjust the relative path if needed
..\..\..\python_embeded\python.exe -s -m pip install -r requirements.txt

# Linux or macOS virtual environment
python -m pip install -r requirements.txt
```

Restart ComfyUI. Startup should report version `0.3.0` and 17 registered nodes.
The post-0.3 `dev` line reports 19 nodes because Generate and Task Profile are
strictly additive.

### Update an existing checkout

```bash
cd ComfyUI/custom_nodes/comfyui-llamacpp
git fetch origin
git switch master
git pull --ff-only origin master
python -m pip install -r requirements.txt
```

Existing 0.2.1 workflows retain released node IDs, socket names, output order,
defaults, and legacy widget positions. Read [the 0.3 migration guide](docs/migration-0.3.md)
before testing important saved workflows.

## Install llama.cpp

Use an official current build from the
[llama.cpp releases](https://github.com/ggml-org/llama.cpp/releases), build it
from source, or install it with your platform package manager. On Windows, the
official CUDA release consists of the matching llama binary and CUDA runtime
archives. Extract both into the same directory.

The exact Windows reference used for 0.3 validation is
[llama.cpp b9957](https://github.com/ggml-org/llama.cpp/releases/tag/b9957)
(`c4ae9a88f`) on an RTX 5090. Its two CUDA 13.3 assets are
`llama-b9957-bin-win-cuda-13.3-x64.zip` and
`cudart-llama-bin-win-cuda-13.3-x64.zip`. This is a tested pin, not the
permanent minimum supported build. b9957 uses companion implementation DLLs
that are absent from the older b8261 deployment, so deploy the complete release
into an empty directory. Do not replace only `llama-server.exe` or overlay it
onto an older llama.cpp directory.

The start nodes resolve the executable in this order:

1. The node's `binary_path` input.
2. The `LLAMA_SERVER_BINARY` environment variable.
3. The compatibility variable `LLAMA_CPP_SERVER`.
4. `llama-server` or `llama-server.exe` on `PATH`.

An explicit path is easiest when several llama.cpp builds are installed. The
pack probes `--version` and `--help`, records the binary identity, and refuses
unsupported requested options before spawning it.

## Model folders

The default location is:

```text
ComfyUI/
└── models/
    └── LLM/
        └── gguf/
            ├── model.gguf
            └── vision-model/
                ├── vision-model.gguf
                └── mmproj-vision-model.gguf
```

The pack also honors Comfy model roots configured as `LLM` or `llm` in
`extra_model_paths.yaml`. If such a root contains a `gguf` child, that child is
used. Paths are resolved through a containment-safe catalog; traversal and
symlink escapes are rejected.

Files whose names contain `mmproj` are listed separately from text/model GGUFs.

### VLM pairing

- Direct mode: leave **Vision Projector** on `(auto)`. The pack reads bounded
  GGUF header metadata and selects one confidently compatible local projector.
  It does not use filenames or folder adjacency alone as compatibility proof.
- If the model is text-only or has no projector candidates and is not known to
  require one, `(auto)` starts explicitly without a projector. If a known VLM
  has no compatible projector, or several distinct projector identities remain
  valid, startup fails before changing the current server and asks for an
  explicit choice. Strong equivalent matches across configured roots are
  grouped and ranked Q8_0, BF16, F16, then F32; an adjacent lower-ranked copy
  does not override that order.
- Select `(none - text only)` to disable vision deliberately. Selecting a
  projector filename uses that exact contained file without substitution.
- Router mode: put each VLM and its matching projector in one dedicated
  subdirectory. The router controls the exact model ID and projector pairing.
- Never pair projectors from a different model size or architecture.

Current automatic matching recognizes projector interfaces it can prove from
local GGUF metadata, including current Gemma 3, Gemma 4, Qwen3-VL, Qwen3.5, and
narrowly identified MiniCPM-V layouts. Unknown metadata never becomes a guess.
The running projector and selection mode are visible in **llama.cpp Server
Status**.

## Quick start

### Direct text workflow

The shortest path is **Workflow > Browse Templates > comfyui-llamacpp > Quick
Text**. Select a GGUF, set the binary only when it is not already resolved, and
queue the workflow. The companion **Setup Check** template diagnoses the binary,
available devices, and configured model roots without starting a server.

To build the same graph manually:

1. Add **Start llama.cpp Server** and select a GGUF.
2. Connect `server_url` to **llama.cpp Basic Prompt**.
3. Connect `response` to **llama.cpp Prompt Output**.
4. Queue the workflow.

The start node is idempotent for the same full configuration. A changed binary
or effective setting performs a coordinated restart. A failed replacement
preflight does not tear down a healthy existing server.

### Canonical Generate workflow

For a new workflow on the post-0.3 line, prefer **llama.cpp Generate**:

1. Add **Start llama.cpp Server** and select a GGUF.
2. Convert Generate's advanced `Server URL` widget to an input and connect the
   Start node's existing `server_url` output, or omit the URL to use the current
   managed runtime.
3. Enter the prompt. Leave Thinking on Auto and Sampling on Default unless the
   model or task needs an explicit override.
4. Optionally connect images, Structured Output, Token Ban, or a Task Profile.
5. Queue the workflow. The node shows bounded live response and thinking text
   and returns response, thinking, and a typed result.

A typed **llama.cpp Connection** can be connected instead of `Server URL`.
Supplying both is rejected before generation. See
[Canonical Generate](docs/canonical-generate.md) for cancellation, profiles,
passive discovery, strict failure, and release-after behavior.

### Router workflow

1. Add **Start llama.cpp Router**.
2. Leave `models_directory` on `(auto)`, or choose the configured GGUF root
   the router should expose.
3. Optionally sequence **llama.cpp Load Model** from its `success` output.
4. Choose the model on a prompt node and generate.
5. Use **llama.cpp Unload Model** for one exact model, or **Release llama.cpp
   VRAM** for all resident router models.

Router load and unload nodes return only after `/models` shows the requested
terminal state. HTTP acceptance by itself is not considered completion.
**llama.cpp List Models** can optionally ask the router to rescan its catalog.
Current llama.cpp combines its cache with one selected local root. Root-level
GGUFs are separate models; each immediate child directory is one logical model
bundle and should contain one base model plus at most one projector. Multiple
base models or projectors in one bundle are ambiguous, and deeper directories
are invisible. Direct-mode dropdowns remain recursive across all configured
roots, so **List Models** is the authoritative router catalog.

For canonical Generate, a local GGUF is resolved under the active router root
and current status metadata is checked against that complete normalized path.
Matching basenames or suffixes in another bundle or configured root do not count
as proof. If a directory-level router ID actually targets another quantization,
generation fails before submitting the prompt. An exact canonical live router
ID remains authoritative when it does not name a local file. Older routers that
omit target metadata retain ID-only compatibility, so one base GGUF per bundle
remains the portable layout.

### Attach to an existing local server

Use `server_url` directly or create a **llama.cpp Connection** profile. An
explicit endpoint that this pack did not start is treated as externally owned.
Native Comfy unload and implicit lifecycle actions never stop or unload it.

## VRAM and lifecycle behavior

ComfyUI does not have a universal custom-runtime unloader. This pack bridges
successful `POST /free` and `POST /api/free` requests into its own lifecycle
coordinator.

| Action | Owned direct server | Owned router | Attached endpoint |
| --- | --- | --- | --- |
| Comfy **Unload Models** | Stops the owned process tree | Unloads resident models, keeps the router when barriers succeed | No action |
| **Release llama.cpp VRAM** | Stops the owned process tree | Unloads all resident models | No action |
| **llama.cpp Unload Model** | Not applicable | Unloads one exact model to terminal state | Not used for implicit ownership |
| **Stop llama.cpp Server** | Stops the process tree | Stops the router process tree | Does not target an attached endpoint |

Release requested during managed generation is deferred until the final active
generation lease exits. Concurrent release requests are coordinated. Direct
release is complete only after the owned process tree is gone. Router release
is complete only after every target reaches a nonresident terminal state; if a
trustworthy router barrier is unavailable, the owned router is stopped as a
safe fallback.

The Release node's Boolean reports whether the request was accepted. Check its
status or `terminal` field before assuming VRAM is already free: `deferred`
and `coalesced` are accepted, nonterminal outcomes.

Set `unload_comfy_models_before_start` on either start node to ask ComfyUI to
evict its managed models and empty its cache before llama.cpp allocates GPU
memory. This is opt-in because it changes the residency of the rest of the
workflow.

See [Lifecycle and VRAM ownership](docs/lifecycle.md) for the full contract and
limitations.

## Node catalog

Nodes keep their released names and are grouped under `LlamaCpp/Runtime`,
`LlamaCpp/Generate`, `LlamaCpp/Router`, and `LlamaCpp/Utilities`. Search aliases
include ordinary terms such as GGUF, VLM, prompt enhancer, JSON Schema, and free
VRAM. Dense nodes show primary controls first; choose **Show Advanced** for the
complete released surface.

| Node | Purpose |
| --- | --- |
| Start llama.cpp Server | Start one positively owned direct server. |
| Start llama.cpp Router | Start one positively owned multi-model router. |
| Stop llama.cpp Server | Explicitly stop the owned direct server or router. |
| Release llama.cpp VRAM | Release direct or router model VRAM while retaining the router when safe. |
| llama.cpp Server Status | Show mode, lifecycle, ownership, PID/group/job state, capabilities, errors, and bounded logs. |
| llama.cpp Connection | Reuse a URL, model, API-key environment name, TLS policy, and deadline. |
| llama.cpp Generate | Recommended strict text, vision, constrained, and prompt generation with live state and a typed result. |
| llama.cpp Task Profile | Store one portable, explicit snapshot of a small user-owned prompt profile. |
| llama.cpp Basic Prompt | Freeform text generation with the common sampling controls. |
| llama.cpp ADV Prompt | Text plus 0 to 10 image sockets and optional full Comfy image batches. |
| llama.cpp ADV++ Prompt | ADV prompting plus templates, token bans, and structured output. |
| llama.cpp Prompt Output | Preview and pass through text, optionally converting common markup to plaintext. |
| llama.cpp List Models | List current router model records and residency states. |
| llama.cpp Load Model | Load one exact router model and wait for a callable state. |
| llama.cpp Unload Model | Unload one exact router model and wait for a nonresident state. |
| llama.cpp Token Count | Call `/tokenize` with model-aware routing. |
| llama.cpp Model Info | Call `/props` and expose model name, context length, and raw properties. |
| llama.cpp Structured Output | Build JSON object, JSON Schema, or GBNF generation constraints. |
| llama.cpp Token Ban | Build llama.cpp text-form logit-bias entries. |

## Important prompt semantics

- `keep_context` maps to llama.cpp `cache_prompt`. It reuses a matching prompt
  prefix in the KV cache. It is not chat history, durable memory, or a session
  database.
- `enable_chaining` remains for saved-workflow compatibility. A connected
  `trigger` socket is what establishes graph ordering.
- Stop sequences accept one entry per line, a JSON string array, or the legacy
  comma-separated form. Use JSON when commas or surrounding whitespace matter.
- Token bans use the same robust list forms and are sent as llama.cpp text-form
  logit-bias entries.
- `image_amount` accepts 0 through 10. All ten sockets exist in Python so saved
  workflows survive frontend reload. `include_image_batch` sends every item in
  a connected Comfy image batch; off preserves the legacy first-image behavior.
- Templates are applied in Python as well as reflected in the UI, so API-format
  and headless workflows behave consistently. Selection fills exact-empty prompt
  fields only and never overwrites a draft. Whitespace remains an intentional
  value. Use the explicit replace/reset action when overwriting both fields is
  intended; the action participates in Comfy undo.
- A generation succeeds only after a valid stream terminal marker. Partial text
  is preserved and labelled when a stream times out, is cancelled, or ends
  without completion.
- Canonical Generate raises on total failure and, by default, on partial output.
  Only `return_marked_partial` lets partial text reach its sockets, and the typed
  result still marks the incomplete state.
- Canonical Default sampling omits the complete sampler group so the selected
  model and server retain their own defaults. Custom sends every displayed
  expert sampler together. Thinking Auto likewise omits an override.

## Authentication and TLS

Secrets are read from environment variables and are not serialized in a
workflow. The default name is `LLAMACPP_API_KEY`.

```bash
# Linux or macOS
export LLAMACPP_API_KEY='your-local-key'

# Windows PowerShell, set before launching ComfyUI
$env:LLAMACPP_API_KEY = 'your-local-key'
```

Canonical Generate accepts only `LLAMACPP_API_KEY` or a namespaced
`LLAMACPP_API_KEY_<UPPERCASE_SUFFIX>` variable name from a workflow. If that
variable resolves to a key, plain HTTP is accepted only for loopback hosts;
authenticated non-loopback endpoints require HTTPS, certificate verification,
and an exact local origin-to-key allowlist:

```bash
# Linux or macOS
export LLAMACPP_API_KEY_LAN='your-remote-key'
export LLAMACPP_REMOTE_AUTH_BINDINGS='{"https://llm.example.test:443":"LLAMACPP_API_KEY_LAN"}'

# Windows PowerShell, set before launching ComfyUI
$env:LLAMACPP_API_KEY_LAN = 'your-remote-key'
$env:LLAMACPP_REMOTE_AUTH_BINDINGS = '{"https://llm.example.test:443":"LLAMACPP_API_KEY_LAN"}'
```

The JSON key is the exact `scheme://host:port` origin. The value is the one
allowed API-key environment name for that origin. A workflow cannot provide or
change this binding. This prevents an imported canonical workflow from selecting
an unrelated environment secret, sending a key over cleartext LAN transport, or
redirecting it to another HTTPS host. Loopback credentials do not need a binding.
Legacy node contracts remain unchanged.

For an owned server, point `api_key_file` at the llama.cpp key file and set
`api_key_env` to the environment variable containing the matching client key.
Connection and prompt nodes also expose `verify_tls` and an overall request
deadline. Commands, status payloads, and bounded server logs redact configured
secret values and common credential-shaped fields.

The namespaced runtime status and release routes use the same network trust
boundary as the rest of ComfyUI. They do not add separate authentication.
Status can include local executable, model, and working-directory paths plus a
redacted log tail, and release can stop this pack's owned runtime. Keep ComfyUI
on loopback or behind authentication that you control when the host is not a
trusted network.

Canonical profile, passive discovery, live restoration, and exact cancellation
routes use the same boundary. Live events target the initiating Comfy client.
Cancellation also checks the live websocket client ID, execution UUID, prompt,
and node identity. This prevents ordinary cross-tab mistakes, but the client ID
is not a separate authentication mechanism. Keep the normal ComfyUI boundary
private or authenticated.

## Templates

Built-in ADV++ templates live in [`web/templates.json`](web/templates.json).
Each entry contains `system_prompt` and `prompt` fields:

```json
{
  "My Template": {
    "system_prompt": "Your system instruction",
    "prompt": "Optional default user prompt"
  }
}
```

Restart ComfyUI after changing the file. Existing templates include
`Image2Prompt` and `Prompt Enhancer`. Selecting a template is a fallback for
blank fields. Selecting `Empty` is a no-op; it clears fields only through the
explicit replace/reset action.

## Examples and validation

ComfyUI discovers the curated workflows in
[`example_workflows/`](example_workflows/). **Setup Check** and **Quick Text** are
the first-run paths; the direct, router, vision, structured-output, and VRAM
handoff workflows retain the released 0.3 examples. Canonical text, vision,
structured, and App Mode examples demonstrate the post-0.3 Generate surface.
The tested frontend 1.45.20 retains terminal text in native jobs and history
output but does not render it inline in App Mode's central result pane. The
canonical workflow exposes transient read-only Generation Status and Live
Response fields that reset on reload and are not serialized.

The [0.4 user acceptance checklist](docs/user-acceptance-0.4.md) covers canonical
workflow compatibility, direct and scoped router release, live Stop, profiles,
App Mode, VLMs, structured output, and the final
terminal LLM-to-diffusion GPU handoff. The accepted stable evidence remains
in the [0.3 validation report](docs/validation-0.3.md). Post-0.3 validation is
recorded separately in the
[canonical Generate validation report](docs/validation-0.4.md).

For failures, start with **llama.cpp Server Status** and
[Troubleshooting](docs/troubleshooting.md). The status node exposes the exact
binary identity, process ownership mode, lifecycle state, pending releases,
and a bounded redacted server-log tail.

## Development

```bash
uv sync --extra dev
uv run python -m pytest -q
uv run ruff check .
uv run ruff format --check .
node --test tests/js/*.test.mjs
for file in web/*.js; do node --check "$file"; done
uv build
git diff --check
```

The test suite contains immutable v0.2.1 and complete 0.3 node/widget contracts,
historical workflow fixtures, current router/client contracts, lifecycle race
tests, canonical request/result/profile round trips, scoped release races,
stream cleanup, live-event bounds, process-tree tests, frontend helper tests,
and package-build checks. CI covers Python 3.10 through 3.14 on Linux plus
Python 3.13 on Windows. Real ComfyUI, current
llama.cpp, Windows Job Object, VLM, and GPU handoff evidence is recorded during
release validation.

## Project documentation

- [Start Here](docs/start-here.md)
- [0.3 migration guide](docs/migration-0.3.md)
- [Post-0.3 migration guide](docs/migration-0.4.md)
- [Canonical Generate](docs/canonical-generate.md)
- [Lifecycle and VRAM ownership](docs/lifecycle.md)
- [Troubleshooting](docs/troubleshooting.md)
- [0.3 validation report](docs/validation-0.3.md)
- [Canonical Generate validation report](docs/validation-0.4.md)
- [User acceptance checklist](docs/user-acceptance.md)
- [0.4 canonical user acceptance](docs/user-acceptance-0.4.md)
- [Changelog](CHANGELOG.md)
- [Research and ecosystem analysis](docs/research/)

## License

[MIT](LICENSE)
