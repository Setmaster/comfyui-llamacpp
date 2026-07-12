# Changelog

## 0.4.0 - Unreleased

### Canonical generation

- Added one strict **llama.cpp Generate** node for text, vision, prompt
  generation, structured output, token bans, typed request provenance, and a
  versioned rich result without removing or changing any released prompt node.
- Added Default/Custom sampling, Auto/Off/On thinking, explicit partial-output
  policy, syntactic JSON validation, Comfy seed control, and actionable
  categorized exceptions instead of success Booleans or error strings.
- Added portable **llama.cpp Task Profile** snapshots plus bounded current-user
  profile refresh and an explicit undoable snapshot update. Freeform remains the
  only bundled profile.
- Added passive managed model discovery with `autoload=false`, Known/Unknown
  facts, raw missing-value retention, runtime-epoch protection, and
  suggestion-only adjacent projectors.
- Added exact router target-evidence validation for current llama.cpp status
  metadata. When that metadata exposes the target, a directory-level router ID
  can no longer silently select a different GGUF. Malformed or inconsistent
  evidence fails without exposing local filesystem paths. Routers without target
  evidence retain ID-only compatibility.
- Added bounded targeted live response/thinking and prompt progress, reload
  restoration, stale-event rejection, and truthful generation-scoped versus
  prompt-scoped Stop labels.
- Added optional exact llama.cpp stream deletion on every terminal path after a
  positive capability probe. Unsupported or Unknown endpoints retain
  prompt-targeted Comfy interruption without claiming node-local cancellation.
- Restricted canonical workflow credential names to the llama.cpp namespace and
  require HTTPS, certificate verification, and an exact local origin-to-key
  binding when authenticated endpoints are outside loopback, without changing
  legacy node behavior.

### Scoped lifecycle

- Added exact generation lease identities and awaitable release handles while
  retaining the existing global native and explicit release paths.
- Direct release-after waits for all direct leases and terminal process-tree
  shutdown. Router release-after waits for the exact model, unloads only that
  model, and never uses process fallback. Attached endpoints are rejected before
  prompt submission.
- Added one bounded scoped-release worker, global-release dominance, and caller
  wait semantics that never cancel accepted cleanup.

### User experience

- Added friendly input labels, focused nested categories, search aliases, and
  progressive disclosure across the existing V1 nodes without changing released
  node IDs, titles, inputs, outputs, defaults, or positional values.
- Aligned frontend template selection with the backend's non-destructive
  exact-empty fallback and added an explicit undoable replace/reset action.
- Expanded Server Status with bounded idle setup diagnostics and a visible,
  copyable summary while preserving its released result tuple.
- Moved workflow templates to canonical `example_workflows`, added Setup Check
  and Quick Text first-run paths, and added a compact Start Here guide.
- Added four current-Comfy canonical Generate workflows with curated 768 by 768
  thumbnails for text, image understanding, structured JSON, and App Mode.
- Exposed transient read-only Generation Status and Live Response fields in the
  canonical App Mode workflow. They remain visible during the current session
  without becoming serialized workflow state.
- Added complete immutable 0.3 contract characterization alongside the existing
  0.2.1 workflow fixtures.
- Added a narrowly guarded browser migration for historical 0.2.1 seed companion
  and persisted Prompt Output layouts while preserving fixed-seed behavior.

## 0.3.0 - 2026-07-11

### Runtime and lifecycle

- Replaced process-name cleanup with positively owned process trees.
- Added deterministic stop escalation and terminal process barriers.
- Added bounded, continuously drained server logs with generation-bound,
  streaming credential redaction across byte, UTF-8, line, and size
  boundaries.
- Added one coordinator for start, stop, replacement, router mutation,
  generation leases, explicit release, and native Comfy release.
- Integrated successful Comfy `/free` and `/api/free` requests while retaining
  all explicit lifecycle nodes.
- Added optional Comfy model eviction before owned LLM startup.
- Added capability probing, full configuration fingerprints, port-collision
  refusal, API-key environment support, TLS policy, and overall deadlines.
- Serialized release against startup, stop, router mutation, and generation so
  native unload cannot observe or leave a half-transitioned runtime.
- Added Linux parent-death supervision, loopback/IPv6 endpoint normalization,
  and accurate Windows Job fallback diagnostics.
- Deferred and coalesced release requests are now reported as accepted,
  nonterminal outcomes.
- Kept runtime diagnostics responsive during startup, shutdown, release, and
  router barriers without weakening lifecycle serialization.
- Added a pre-spawn Linux pidfd capability gate, launch-generation pidfd
  ownership, `waitid(P_PIDFD)` observation and consumption, and atomic
  pidfd process-group signaling on Linux 6.9 or newer. Older kernels use a
  freshly revalidated `killpg` fallback. Missing, invalid, externally reaped,
  or indeterminate authority fails closed without numeric PID fallback.

### llama.cpp protocol

- Updated router operations to `/models/load` and `/models/unload`.
- Added exact ID and alias resolution with ambiguity errors.
- Added terminal load/unload polling and normalized current router states.
- Added shared `/props` and `/tokenize` clients.
- Added optional router catalog rescanning through `/models?reload=1`.
- Added populated-root auto-selection and an explicit configured-root selector
  for llama.cpp's one-level `--models-dir` router scan.
- Added robust SSE parsing, terminal success semantics, partial-output metadata,
  cancellation, response closure, and exact output-whitespace preservation.
- Added explicit UTF-8 decoding from raw chat and router SSE bytes instead of
  relying on `requests` charset inference for `text/event-stream`.
- Added current build-number parsing, symbolic GPU-layer capability checks, and
  protection against typed/security flags being duplicated in `extra_args`.

### Nodes and frontend

- Added Connection, Release Runtime, Token Count, Model Info, and Structured
  Output nodes, for 17 total nodes.
- Consolidated Basic, ADV, and ADV++ through one generation path.
- Declared all ten VLM image sockets in Python and fixed zero-image handling.
- Added optional full Comfy image-batch input.
- Applied ADV++ templates in Python for frontend and API-format consistency.
- Replaced prototype-level frontend mutation with per-node lifecycle hooks.
- Made Prompt Output nonserialized and resilient to tab switching.
- Added descriptions and input/output tooltips across all 17 nodes, plus native
  Comfy toast notices for queued and failed release events.
- Kept attached-endpoint model IDs independent from any unrelated owned router
  catalog.

### Compatibility and packaging

- Preserved every 0.2.1 released node ID, output, default, socket, and legacy
  primitive widget prefix.
- Restored the historical process-wide manager identity for direct no-argument
  construction while retaining independent dependency-injected test managers.
- Added 0.2.1 contract and saved-workflow fixtures.
- Added containment-safe Comfy model-folder integration and exact router model
  mapping.
- Added MIT license, single-source 0.3.0 versioning, locked dependencies,
  Registry metadata, package builds, Ruff, pytest, JavaScript tests, Linux
  Python 3.10 through 3.14 CI, and Windows Python 3.13 CI.

### Documentation

- Replaced the obsolete README.
- Added migration, lifecycle, troubleshooting, examples, and user-acceptance
  documentation.

## 0.2.1

- Last stable `master` baseline before the broad 0.3 refactor.
