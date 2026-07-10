# Changelog

## 0.3.0 (development)

### Runtime and lifecycle

- Replaced process-name cleanup with positively owned process trees.
- Added deterministic stop escalation and terminal process barriers.
- Added bounded, continuously drained, secret-redacted server logs.
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

### Compatibility and packaging

- Preserved every 0.2.1 released node ID, output, default, socket, and legacy
  primitive widget prefix.
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
