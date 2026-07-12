# Proposal: ux-work-block-a

Date: 2026-07-11

> Do not put secrets in this folder. Use 1Password.

## Why

- The 0.3 runtime is reliable and released, but its capability remains hidden
  behind dense legacy widgets, one flat menu, weak search language, destructive
  frontend templates, and an engineering-shaped first-run path.
- Current Comfy V1 supports input labels, advanced metadata, aliases, nested
  categories, and custom workflow templates without a full V3 migration.
- The accepted product review ranks these presentation and onboarding fixes ahead
  of new backend breadth.

## Scope

- Preserve all released execution and saved-workflow contracts.
- Add input display labels, advanced metadata, search aliases, and nested
  Runtime, Generate, Router, and Utilities categories.
- Add an instance-scoped classic-renderer compatibility helper for advanced
  widgets, linked controls, and the seed companion.
- Make ADV++ template selection exact-empty-only by default, with strict frontend
  validation and an explicit undoable replace/reset action.
- Extend Server Status with bounded idle setup diagnostics and a visible UI result.
- Move workflows to canonical `example_workflows/`, retain the five existing
  validation workflows, and add Setup Check plus Quick Text with thumbnails.
- Add a compact Start Here guide and update packaging, docs, and tests.

## Non-goals

- No canonical Generate node, live streaming UI, node-local cancellation, rich
  result type, model refresh, shared profiles, or release-after-generation.
- No provider matrix, cloud API, embedded llama-cpp-python, agents, RAG, MCP,
  prompt database, persistent sessions, video, model download manager, or App
  Mode application.
- No node ID, node display name, output, input name/type/default, or positional
  widget changes.

## Risks

- Classic LiteGraph and Nodes 2.0 read advanced state differently.
- Comfy adds a frontend seed companion that is absent from backend schemas.
- An asynchronous template fetch can apply a stale selection without a revision
  guard.
- Idle binary/device probes and recursive model scans must be bounded.
- Workflow directory renaming changes direct repository URLs and package data.
- Screenshots can accidentally expose local paths unless captured deliberately.

## Rollback

- Revert the Work Block A commits on `dev`, or reset the installed validation
  clone to immutable tag `0.3.0`. The release tag and `master` are never moved.
