# Design: canonical-generate-work-block-b

Date: 2026-07-11

## Approach

Build one additive strict facade over the accepted 0.3 foundations:

1. Resolve either the existing typed Connection, an existing Start-node URL, or
   the current managed runtime.
2. Resolve a canonical router model before lease admission.
3. Prepare a JSON-safe request specification and an execution-only payload whose
   image bytes are never retained in result metadata.
4. Execute through the existing payload builder and streaming client while a
   managed exact-target lease is active.
5. Publish bounded live snapshots through a private active-execution registry.
6. Apply the explicit partial policy, syntactic JSON validation, and strict error
   categories.
7. If requested, arm scoped cleanup before lease exit, then withhold outputs
   until its terminal release handle completes.
8. Return response, thinking, and one typed rich result.

Legacy `run_prompt()` and `legacy_result()` remain unchanged.

## Data model changes

### Connection

Keep `LlamaCppConnectionProfile` at its existing import path and field order.
Add versioned `as_dict()` and `from_dict()` methods. Only the environment-variable
name is serialized, never the resolved secret.

### Request and result

Add `generation/contracts.py` with:

- `ThinkingMode`, `SamplingMode`, `PartialOutputPolicy`, `ReleasePolicy`, and
  `ErrorCategory`;
- `SamplingSettings` and JSON-safe `GenerationRequestSpec`;
- `GenerationUsage`, `GenerationTiming`, `GenerationErrorInfo`,
  `GenerationReleaseInfo`, and `GenerationResult`.

The request specification records exact prompt/effective system text, requested
model, profile ID/hash, thinking/sampling values, seed, cache, stops, image count,
structured-output kind, token-bias count, release/partial policy, and a
secret-free connection snapshot. Encoded image data stays execution-only.

The result records complete/partial/cancelled state, response/thinking, requested
and effective model, profile provenance, seed/image count/constraint, normalized
usage, finish reason, response ID, chunks, terminal evidence, first-chunk and
generation/release/total timing, normalized error, warnings, and release evidence.
It exposes deterministic `as_dict()` and `to_json()` methods.

### Profiles

Add frozen `TaskProfileSnapshot` with schema version, ID, name, description,
system prompt, prompt prefix, and prompt suffix. The node stores one compact JSON
snapshot as its only primitive. Execution never looks up a mutable profile name.

Per-user `profiles.json` is loaded through Comfy's user manager by a GET-only
route. Parsing is bounded by file bytes, profile count, ID/name/description/text
lengths, and exact allowed keys. No includes, inheritance, variables, code, or
directory scan are supported.

### Discovery

Add typed `Fact[T]` values whose states are only Known or Unknown. Add
`RuntimeModelDescriptor`, `ProjectorSuggestion`, and
`RuntimeDiscoverySnapshot`. Facts never turn missing metadata into unsupported.

Discovery precedence is selected-model passive props, active router model list,
identity-only endpoint fallback, owned launch configuration, and finally a
clearly labelled offline local catalog. Runtime epochs prevent responses from
mixing two different owned runtimes.

### Live execution

Add an in-memory `LiveGenerationRegistry` keyed by a fresh execution UUID and
capturing Comfy prompt/dynamic/display/real/parent/list identities when available.
Snapshots retain bounded valid UTF-8 tails for response and thinking and contain
only normalized state.

### Scoped lifecycle

Add immutable `GenerationLeaseToken`, `ReleaseTarget`, and terminal
`ReleaseHandle`. Track active leases by ID, direct count, router count by exact
canonical model, runtime epoch, and scoped operation key.

Scoped operations are serviced by one bounded coordinator-owned worker. A
handle exists before lease exit. Arming blocks new work only in its scope;
already admitted work completes. Caller wait timeout or interruption never
cancels accepted cleanup.

## API / surface changes

### Nodes

`LlamaCppGenerate`:

- required: prompt;
- optional: typed connection, task profile, advanced URL, model, system prompt,
  Auto/Off/On thinking, max tokens, Default/Custom sampling, expert sampler
  controls, Comfy random/fixed seed, prefix cache, stops, auth environment, TLS,
  timeout, 0 to 10 images plus full batches, release-after-generation, explicit
  partial policy, structured output, and token bans;
- hidden: unique ID and dynamic prompt;
- outputs: response, thinking, and `LLAMACPP_GENERATION_RESULT`;
- no success Boolean because normal output means complete or explicitly accepted
  partial data.

`LlamaCppTaskProfile`:

- one serialized compact profile snapshot;
- one `LLAMACPP_PROFILE` output;
- frontend-only selector, Refresh, explicit undoable Update Snapshot, and
  current/missing/changed status.

The 17 legacy nodes retain exact public contracts.

### Events and routes

Use `llamacpp.generation` bounded snapshot events. Emit at no more than 8 Hz,
cap each text pane at 64 KiB, cap encoded events below 160 KiB, and target only
the initiating Comfy client. Frontend-only widgets set both serialization flags
false.

Add:

- `GET /llamacpp/generation/active` for bounded same-client restoration;
- `POST /llamacpp/generation/cancel` for exact execution cancellation;
- `GET /llamacpp/runtime/discovery` for managed passive discovery;
- `GET /llamacpp/profiles` for bounded current-user profiles.

Cancel routes accept only execution identity. They never accept a URL, model,
credential, or arbitrary upstream path.

### Cancellation

Probe `POST /v1/streams/lookup` with a random nonexistent ID. Cache by connection
fingerprint for about 60 seconds:

- 200 plus a JSON list means exact stream control is supported;
- 404/405 means unsupported;
- 401/403 remains an auth/configuration error;
- malformed success means Unknown.

For supported llama.cpp, use an internal UUID in `X-Conversation-Id`, request
prompt progress and a one-second SSE ping, and expose `Stop generation`.
Cancellation sets the local token and issues exact upstream DELETE. Every exit,
including success, error, timeout, node-local cancel, and Comfy `BaseException`,
issues DELETE again in `finally` before releasing the lease.

For unsupported/unknown endpoints expose `Stop Comfy job` and call Comfy's
prompt-targeted interrupt. Never label that action node-local and never silently
escalate a failed exact DELETE into global interruption.

### Release

Keep current global `request_release()`, native `/free`, Stop, explicit Release,
router-wide unloading, and owned-router fallback unchanged.

The canonical private path has these semantics:

- direct: wait for all active direct leases, stop the owned process once, and
  complete only after process-tree terminal evidence;
- router: wait only for the exact model's leases, unload only that ID, and fail
  closed without process fallback on unknown/mismatched/nonterminal evidence;
- attached: reject the canonical release promise before generation;
- native global release dominates queued scoped work and may satisfy handles
  through its terminal result.

Process/model terminal evidence is not called proof of driver-memory convergence.

## Migration notes

- There is no saved-workflow migration for the new nodes.
- Existing Start URL outputs connect directly to Generate's advanced URL input.
- Existing Connection outputs connect to Generate's typed Connection input.
- Supplying both inputs is an actionable ambiguity error.
- Legacy Basic/ADV/ADV++ remain visible and loadable. Deprecation is deferred
  until migration evidence exists.
- Existing 0.2.1 seed migration remains fixed. New Generate seeds use Comfy's
  normal randomize companion by default and allow explicit fixed mode.

## Risks

- The resumable stream API is upstream-internal and may change. Capability
  probing, optional use, strict final cleanup, and fallback labels contain this.
- Execution context helpers and PromptServer/frontend APIs may change. Wrappers
  must tolerate absence and preserve headless generation.
- Global/scoped release interaction can deadlock or mutate stale runtimes without
  epoch and operation ordering tests.
- A browser reload or duplicate extension can duplicate listeners/widgets unless
  registration and restoration are idempotent.
- Full profile snapshots and live preview text can bloat workflows/events unless
  size limits are enforced at every boundary.
