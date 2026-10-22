# Example workflows

ComfyUI lists these workflows under **Workflow > Browse Templates >
comfyui-llamacpp**. Each one is a small, working recipe; the workflows that use
earlier prompt nodes also show those nodes still work.

## First run

- `setup-check.json`: inspect binary, device, model-root, model and projector
  readiness without starting a server.
- `quick-text.json`: the smallest owned-server text path: Start, Basic Prompt
  and Prompt Output.

## Generate

- `canonical-text.json`: recommended text generation with a portable Freeform
  profile snapshot, strict errors, live status and managed release at the end.
- `canonical-vlm-image-understanding.json`: connect one Comfy image to Generate
  for factual local image understanding.
- `canonical-structured-json.json`: connect a strict JSON Schema and validate the
  final response as JSON.
- `canonical-app-mode.json`: a small App Mode surface exposing selected model and
  generation controls plus live status and response, with Generate itself as
  the native output-history node.

## Composition, history and captions

- `graph-structured-field.json`: use core JSON extraction to read a generated
  `positive_prompt` field and serialize the full typed result for inspection.
- `graph-protected-literal-draft.json`: append fixed literal text after
  generation with core text nodes, preserving its exact characters.
- `graph-frozen-render.json`: a separate core-only render graph. Paste and save
  a reviewed prompt; requeueing this workflow makes no LLM request. Select an
  installed compatible SD1.5/SDXL checkpoint.
- `messages-few-shot.json`: supply explicit few-shot history, generate a draft,
  append its exact user/assistant turns, then refine. The last request releases
  the model; an earlier chain failure requires an explicit unload.
- `caption-batch-aligned.json`: join two images and generate independent
  captions with explicit IDs and seeds, strict failures and one group release.

## Earlier prompt nodes

- `direct-text.json`: start one owned model, generate, inspect tokens and
  properties, and preview output.
- `router-text.json`: start the router, load an exact model, generate, unload
  the model and stop the router.
- `vlm-image-to-prompt.json`: start a VLM with an explicit projector and send a
  Comfy image through ADV++ Image2Prompt.
- `structured-output.json`: constrain ADV++ output with a nested JSON Schema.
- `vram-handoff.json`: optional Comfy pre-eviction before the LLM starts, then
  explicit LLM release. Native **Unload Models** works at the same point.

## Before you queue

Model and projector names are local installation choices. After loading an
example, select entries that exist in your configured `LLM/gguf` roots, and set
`binary_path` if Setup Check cannot resolve `llama-server` from the environment.
For the vision examples, choose an image from your own ComfyUI input folder.

The Generate examples leave **Vision Projector** on `(auto)`. A text model then
starts without a projector, and a supported vision model gets the one installed
projector that GGUF metadata proves compatible. A missing or ambiguous match
fails before launch; select an exact projector file, or `(none - text only)` to
disable vision deliberately.

The saved Freeform profile in the Generate examples is a portable no-op
snapshot, so running them never depends on a profile file from the machine that
created the workflow.

## Compatibility notes

Some Generate examples were saved before the **Earlier Messages** and **Budget
Policy** inputs existed. They use no history, keep budgeting off, and import
with their saved widget positions intact. Budgeting is off by default on
Generate; the separate Request Budget node defaults to `report` and never
generates text.

The examples built on earlier prompt nodes are kept unchanged from their
original release, apart from explicit model-root and catalog-reload values in
`router-text.json`. Tests hold them byte for byte, and every workflow's socket
types are checked against the pack and core nodes.

App Mode does not render terminal text inline, but it remains available in
native jobs and history output. The App Mode example therefore exposes
read-only Generation Status and Live Response fields for the current session.
They reset on reload and are not saved or written as output files.

## Further reading

Full recipe contracts: [graph composition](../docs/graph-composition.md),
[explicit messages](../docs/messages.md) and
[caption groups](../docs/captions.md). Focused guides cover
[router presets](../docs/router-presets.md),
[Request Budget](../docs/request-budget.md),
[task profile authoring](../docs/task-profile-authoring.md) and
[audio transcription](../docs/audio-transcription.md), which needs the
documented model pair and is never enabled implicitly by these examples.

The examples are demonstrations, not acceptance tests. Execution evidence and
capability limits are in the
[feature validation report](../docs/validation-2026-10-features.md), and the
manual checks are in the
[Generate user acceptance checklist](../docs/user-acceptance-0.4.md).
