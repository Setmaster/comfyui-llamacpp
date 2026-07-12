# Spec: ux-work-block-a

Date: 2026-07-11

## Requirements

- R1: Every node ID, display mapping, function, output tuple, input name/type,
  default, call order, and complete 0.3 primitive widget order remains compatible.
- R2: Every input has a concise display label and tooltip. Dense nodes mark only
  secondary primitive controls advanced; sockets remain available.
- R3: Categories are exactly `LlamaCpp/Runtime`, `LlamaCpp/Generate`,
  `LlamaCpp/Router`, or `LlamaCpp/Utilities`, with task-language search aliases.
- R4: Both current renderers hide advanced values by default, retain them through
  save/reload, show linked advanced controls, and treat the seed companion as one
  disclosure group.
- R5: Selecting a template fills only exact-empty string fields independently.
  Empty, unknown, malformed, stale, whitespace, and nonstring cases never destroy
  user text.
- R6: Explicit replace copies both selected template fields exactly, is
  nonserialized, and participates in Comfy undo.
- R7: Server Status keeps its three outputs and appends only an optional binary
  path. Idle execution reports setup readiness, bounded binary/version/device
  evidence, root/model/projector counts, warnings, and compact JSON.
- R8: Setup diagnostics never launch a server, dump environment contents, infer
  projector compatibility, or alter the lifecycle status endpoint.
- R9: The canonical workflow template directory contains each of seven workflows
  once. Setup Check and Quick Text have bounded matching thumbnails and pass fresh
  browser import.
- R10: Documentation provides a shortest-path Start Here and preserves detailed
  runtime, router, VLM, migration, and troubleshooting references.

## Scenarios

- A new user searches for `GGUF`, opens Start Server, sees five primary controls,
  and can reveal every released expert value without losing it.
- A linked advanced Server URL remains visible while the node is collapsed to
  primary controls.
- A user with a draft selects Prompt Enhancer. The draft survives, the blank
  system prompt fills, and selecting Empty does nothing.
- The user explicitly replaces template fields, then uses Ctrl+Z to recover the
  exact previous draft.
- With no server running, Setup Check reports whether llama-server and models are
  ready and gives one actionable warning for a missing binary or model catalog.
- Quick Text opens from Browse Templates and produces real local output after the
  user selects an installed model.
- A saved 0.2.1 or 0.3 workflow loads and re-exports with the same values, input
  sockets, and output wiring under both renderers.

## Open questions

- None. The accepted Frontier Review and current source evidence resolve the
  renderer, compatibility, template, diagnostic, and onboarding boundaries.
