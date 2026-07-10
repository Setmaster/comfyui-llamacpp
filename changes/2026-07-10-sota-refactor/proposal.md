# Proposal: sota-refactor

Date: 2026-07-10

> Do not put secrets in this folder. Use 1Password.

## Why

- The project has a valuable niche: broad local GGUF text and vision interaction, advanced sampling, and direct llama-server ownership in a compact ComfyUI node pack.
- Its released implementation predates major changes in ComfyUI, the frontend, llama-server router APIs, multimodal behavior, Registry packaging, and user expectations around GPU handoff.
- The current lifecycle can kill unrelated processes, cannot reliably own router descendants, may deadlock on an undrained pipe, and reports router unload before release is complete.
- The current dirty feature bundle adds useful capabilities but can reinterpret saved workflow widgets and does not make dynamically created image sockets visible to Python.
- The user wants a current, polished, state-of-the-art refactor that is implemented and tested on a remote `dev` branch before hands-on blessing.

## Scope

- Preserve released workflow and Python-import compatibility while restructuring the internals.
- Add immutable runtime configuration, binary capability probing, safe process ownership, bounded log capture, a locked lifecycle service, and active-generation leases.
- Implement current llama-server health, props, auth, tokenize, streaming, router list/load/unload, model-state polling, structured-output, and multimodal contracts.
- Integrate Comfy's native unload requests with owned llama-server release and retain explicit Stop Server and Unload Model nodes.
- Add optional reverse handoff so an owned llama-server can request Comfy-managed model eviction before allocation.
- Use Comfy `folder_paths` for model discovery with containment and exact router identity rules.
- Consolidate prompt schemas, payloads, image conversion, templates, errors, connection handling, and frontend behavior.
- Finish the existing Model Info, Token Count, Structured Output, stop-sequence, sampling, and startup-hardening work behind tests.
- Add compatibility fixtures, unit and integration tests, CI, packaging validation, documentation, examples, and live smoke checks.

## Non-goals

- Do not merge to `master` or publish to the Comfy Registry.
- Do not become an all-in-one agent, RAG, MCP, database, social, audio, video, or cloud-provider suite.
- Do not embed `llama-cpp-python` or another model runtime inside ComfyUI.
- Do not require cloud APIs or add raw secrets to workflows.
- Do not perform a full V3 node migration in this release.
- Do not promise persistent conversation history under the existing `keep_context` input. It remains prompt-prefix cache reuse.

## Risks

- Saved workflows are sensitive to primitive widget order, node IDs, socket names, and output tuple order.
- Process ownership differs materially between POSIX process groups and Windows Job Objects.
- ComfyUI has no official external-resource unload callback. The backend middleware bridge must be narrow, idempotent, fail-open, and covered against current Comfy versions.
- Current llama-server evolves quickly. Optional CLI flags need capability probes and protocol tests rather than version guessing.
- A broad refactor can hide behavior regressions unless compatibility characterization precedes extraction.
- Driver memory telemetry may lag real release. Functional resource reallocation is the primary live assertion.

## Rollback

- `master` remains untouched. The entire refactor can be abandoned by deleting the remote and local `dev` branch.
- Each implementation wave is committed separately so individual slices can be reverted without discarding later research.
- Root compatibility facades allow internal modules to be rolled back independently of external imports.
