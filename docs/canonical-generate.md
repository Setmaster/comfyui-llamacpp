# Canonical Generate

**llama.cpp Generate** is the new recommended generation node on the `dev`
line. The released Basic, ADV, and ADV++ nodes remain available and retain their
saved-workflow contracts.

## Choose a connection

Generate resolves one connection in this order:

1. A connected **llama.cpp Connection** value.
2. The advanced `Server URL` input, including the URL output of either Start
   node.
3. The currently owned runtime when neither input is supplied.

Do not supply both a Connection and a nonempty Server URL. Generate reports that
as an ambiguous request before contacting a server. API key values are resolved
from the selected environment-variable name on the backend and are never stored
in the workflow, request result, live event, or profile snapshot.

Canonical workflows may name only `LLAMACPP_API_KEY` or
`LLAMACPP_API_KEY_<UPPERCASE_SUFFIX>`. A resolved key may use plain HTTP only on
loopback. Authenticated non-loopback endpoints require HTTPS, enabled certificate
verification, and an exact server-side origin-to-key binding in
`LLAMACPP_REMOTE_AUTH_BINDINGS`. This local JSON object maps an origin, including
its port, to the only allowed API-key environment-variable name:

```bash
export LLAMACPP_API_KEY_LAN='your-key'
export LLAMACPP_REMOTE_AUTH_BINDINGS='{"https://llm.example.test:443":"LLAMACPP_API_KEY_LAN"}'
```

The binding cannot be supplied by a workflow. It prevents an imported workflow
from redirecting a locally resolved credential to another HTTPS destination.
Loopback credentials do not require a binding. These checks run before client
creation and do not alter the compatibility behavior of legacy nodes.

The model selector can be refreshed against the managed runtime without loading
a model. Refresh calls `/models` without reload and calls `/props` with
`autoload=false`. A saved value that is absent from the response remains visible
as Missing instead of being silently replaced. Attached connections remain
usable, but the browser Refresh surface intentionally does not probe arbitrary
attached URLs.

## Primary controls

- `Thinking: Auto` leaves model and chat-template defaults alone. `Off` and `On`
  send an explicit Boolean override.
- `Sampling: Default` omits the complete custom sampler group. `Custom` sends
  temperature, top P, top K, min P, repetition penalty, presence penalty, and
  frequency penalty together.
- Seed uses ComfyUI's normal fixed, increment, decrement, and randomize control.
- Prefix cache maps to llama.cpp `cache_prompt`. It is not chat history or a
  persistent session.
- Image Inputs accepts 0 through 10 sockets. Full Batch sends every frame from
  each connected Comfy IMAGE; otherwise only the first frame from each input is
  sent.
- Structured Output and Token Ban reuse the existing typed utility nodes.

## Results and strict failures

Generate returns:

1. response text;
2. separately reported thinking text;
3. a typed `LLAMACPP_GENERATION_RESULT` value.

The result is versioned and JSON safe. It records normalized request provenance,
model identity, profile ID and content hash, seed, image and constraint counts,
usage, finish evidence, timing, warnings, error category, and release evidence.
It does not retain encoded images, resolved API keys, arbitrary server bodies,
process objects, or live preview state.

A total failure raises and stops downstream execution. The default partial
policy also raises. `Return Marked Partial` is the only setting that permits
incomplete response text to reach output sockets, and the typed result marks it
as partial or cancelled. JSON object and JSON Schema modes additionally require
the completed response to parse as JSON.

## Live preview and Stop

During execution the node shows bounded response and thinking tails, prompt
progress when reported by llama.cpp, phase, elapsed time, and release state. The
preview is browser-only and is never serialized. Events are sent only to the
initiating Comfy client, are limited to 8 updates per second, and reject stale
execution sequences.

Current llama.cpp builds may expose an internal resumable-stream interface. A
positive capability probe lets the node say **Stop generation** and cancel only
its private upstream conversation ID. The backend deletes that exact stream on
every exit path, including normal success, error, timeout, local cancellation,
and Comfy interruption.

The final live snapshot includes the bounded cleanup attempt count and whether
upstream confirmed deletion. An unconfirmed attempt remains visible in status
and result warnings. Local cancellation or a closed response is never used as a
substitute for upstream confirmation.

When exact stream control is unsupported or Unknown, the button says **Stop
Comfy job** and uses Comfy's prompt-targeted interrupt. It does not claim that
only one node or upstream generation was stopped. A failed exact DELETE is never
silently escalated into a whole-job interrupt.

The upstream stream-control API is explicitly internal. Generate probes it and
falls back honestly rather than treating it as a permanent llama.cpp contract.

## Release after generation

`Release After Generation` is available only for a runtime positively owned by
this pack. It is rejected before prompt submission for attached endpoints.

- Direct mode waits for every already-admitted direct generation, stops the
  exact owned process tree once, and withholds outputs until terminal process
  evidence is available.
- Router mode resolves one exact model ID before generation, waits for that
  model's leases, unloads only that model, and never stops the router as a
  fallback for scoped cleanup.
- A global native or explicit release can supersede queued scoped cleanup and
  satisfy its waiters through the global terminal result.

Caller wait timeout or interruption stops only that caller's wait. Accepted
cleanup continues in the lifecycle coordinator. Process or model exit is not
described as proof that GPU-driver memory has already converged; use the backend
memory tool and a subsequent allocation for that validation.

## Task profiles

**llama.cpp Task Profile** stores one complete compact snapshot in the workflow.
Execution uses that saved content and never reloads a mutable profile name. A
profile can wrap the user prompt and fill an exactly empty system prompt. It
cannot change sampling, model, seed, images, constraints, release, or partial
policy.

Freeform is the only built-in profile. Optional user profiles live in the
current Comfy user's data directory at:

```text
comfyui-llamacpp/profiles.json
```

With Comfy's ordinary default user directory this resolves to
`ComfyUI/user/default/comfyui-llamacpp/profiles.json`. A custom user directory or
multi-user configuration changes the user root, and the route resolves it from
the current request rather than using one process-wide file.

The document has this bounded form:

```json
{
  "schema_version": 1,
  "profiles": [
    {
      "schema_version": 1,
      "id": "caption",
      "name": "Caption",
      "description": "Describe an image without inventing details.",
      "system_prompt": "Report only visible evidence.",
      "prompt_prefix": "Describe this image: ",
      "prompt_suffix": ""
    }
  ]
}
```

Refresh reads the current user's file without restarting Comfy. Selecting an
entry does not mutate the workflow. **Update Saved Snapshot** is the explicit,
undoable copy action. The status distinguishes Current, Changed, Missing, and
Invalid local state while the saved snapshot remains authoritative.

## App Mode

Canonical examples expose the Generate prompt and selected primary controls as
App Mode inputs and use Generate itself as the native text-output node. The
canonical App Mode workflow also exposes **Generation Status** and the bounded,
read-only **Live Response** as current-session feedback. That preview is
transient and is not durable output history after a reload. Generate still
reports its terminal response through Comfy's native output-history and jobs API
contract. Some current Comfy frontend builds do not render inline text from that
contract in App Mode's central result pane, so the live response remains visible
in the app controls without patching Comfy or writing surrogate files.
