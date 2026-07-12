# Proposal: canonical-generate-work-block-b

Date: 2026-07-11

> Do not put secrets in this folder. Use 1Password.

## Why

- The released Basic, ADV, and ADV++ nodes share one generation core but expose
  historical feature tiers. A new user must understand project history before
  choosing a node.
- The runtime already has strong external-process ownership, exact terminal
  router barriers, native Comfy unload integration, multimodal payloads,
  structured output, and partial-stream semantics. The missing layer is a
  compact canonical user surface.
- Long local generations currently appear frozen, total failures can become
  ordinary output strings on legacy nodes, runtime facts are weakly surfaced,
  and convenient release-after-generation cannot be expressed safely by the
  existing global lease counter.

## Scope

- Add `LlamaCppGenerate` as the canonical strict successor for new workflows.
- Add `LlamaCppTaskProfile` as a small user-owned snapshot mechanism.
- Add typed, JSON-safe internal request and public rich-result contracts.
- Add bounded execution-scoped preview, progress, timing, and truthful
  cancellation.
- Add passive model/capability discovery with Known/Unknown facts, missing saved
  state, and suggestion-only projector guidance.
- Add private exact-target generation leases and terminal scoped release handles.
- Add App Mode workflows and complete documentation, tests, browser/runtime
  evidence, packaging, review, CI, installed-clone, and KB closeout.

## Non-goals

- No edits to the public schemas or error behavior of the 17 released nodes.
- No cloud providers, agents, RAG, MCP, prompt database, hidden sessions, model
  store, downloads, audio, video, message arrays, or prompt studio.
- No public request builder, result unpacker, scoped-release route, or profile
  management application.
- No automatic projector selection or capability guesses from filenames.
- No profile content other than no-op Freeform before a fixed bakeoff.
- No V3 migration, `master` merge, `0.3.0` mutation, or Registry publication.

## Risks

- llama.cpp resumable stream cancellation is an internal, optional interface.
  It must be capability-probed and every opted-in request must issue an
  idempotent upstream DELETE in `finally`.
- Comfy live-event and execution-context APIs are de facto integration points,
  not stable public contracts. All integration must be narrow, idempotent, and
  fail open without affecting headless execution.
- Exact per-model release introduces concurrency and epoch races. The scoped path
  must be private, bounded, fully terminal, and unable to inherit router-wide
  process fallback.
- Dynamic widgets differ between classic and Nodes 2.0. State, link visibility,
  serialization, and loaded-node sizing require real browser proof.
- User profile files are untrusted local input. Parsing, count, byte, key, and
  text limits are mandatory.

## Rollback

- Revert the Work Block B commits on `dev`.
- The immutable `0.3.0` tag and `master` remain unchanged throughout.
- Existing workflows continue to use the 17 legacy nodes and do not depend on
  either new node.
