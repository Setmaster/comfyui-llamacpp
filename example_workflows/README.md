# Example workflows

This directory ships with the unreleased 0.4 candidate. ComfyUI discovers it in
**Workflow > Browse Templates**. The earlier canonical workflows have recorded
frontend round-trip evidence. The five new graph, history and caption recipes
are undergoing integration; schema checks do not establish native inference or
browser acceptance. Stable 0.3 instead ships five JSON workflows in `examples/`,
which users open manually.

- `canonical-text.json`: canonical text generation with a portable Freeform
  profile snapshot, strict errors, live status, and terminal managed release.
- `canonical-vlm-image-understanding.json`: connect one Comfy image to canonical
  Generate for factual local image understanding.
- `canonical-structured-json.json`: connect a strict JSON Schema and validate the
  terminal response as JSON.
- `canonical-app-mode.json`: a small App Mode surface exposing selected local
  model and generation controls plus transient live status and response, with
  Generate itself as the native output-history node.
- `graph-structured-field.json`: use core JSON extraction to read a generated
  `positive_prompt` field and serialize the full typed result for inspection.
- `graph-protected-literal-draft.json`: append fixed literal text after generation
  with core text nodes, preserving its exact characters.
- `graph-frozen-render.json`: a separate core-only render graph. Paste and save
  a reviewed prompt; requeueing this workflow makes no LLM request. Select an
  installed compatible SD1.5/SDXL checkpoint.
- `messages-few-shot.json`: supply explicit few-shot history, generate a draft,
  append its exact user/assistant turns, then refine. The last request releases
  the model; an earlier chain failure requires an explicit unload.
- `caption-batch-aligned.json`: join two images and generate independent captions
  with explicit IDs and seeds, strict failures and one terminal group release.
- `setup-check.json`: inspect binary, device, model-root, model, and projector
  readiness without starting a server.
- `quick-text.json`: the smallest owned-server text path: Start, Basic Prompt,
  and Prompt Output.
- `direct-text.json`: start one owned model, generate, inspect tokens/properties,
  and preview output.
- `router-text.json`: start the router, load an exact model, generate, unload the
  model, and stop the router.
- `vlm-image-to-prompt.json`: start a VLM with an explicit projector and send a
  Comfy image through ADV++ Image2Prompt.
- `structured-output.json`: constrain ADV++ output with a nested JSON Schema.
- `vram-handoff.json`: demonstrate optional Comfy pre-eviction and explicit LLM
  release. Native **Unload Models** can be used at the same point.

Model and projector names are local installation choices. After importing an
example, select entries that exist in your configured `LLM/gguf` roots and set
`binary_path` when Status cannot resolve `llama-server` from the environment.
All canonical examples leave **Vision Projector** on `(auto)`. For a text model,
that starts explicitly without a projector. For a supported VLM layout, the
pack selects one compatible installed projector only when GGUF metadata proves
the match. Ambiguous or missing known-VLM matches fail before launch. You can
select an exact projector file instead, or choose `(none - text only)` to
disable vision deliberately. Select an image from your own ComfyUI input folder
before running the VLM example.

The four `canonical-*.json` examples target 0.4.0. Their saved Freeform profile
is a portable no-op snapshot, so execution never depends on a mutable profile
file from the machine that created the workflow. Setup Check and Quick Text were
also added after 0.3. The five legacy generation examples originate from 0.3.0:
`direct-text`, `structured-output`, `vlm-image-to-prompt`, and `vram-handoff`
remain byte-identical to that tag; `router-text` adds explicit model-root and
catalog-reload widget values. Tests preserve the resulting seven noncanonical
candidate assets byte-for-byte.

The earlier canonical examples intentionally omit the newly appended **Earlier
Messages** and **Budget Policy** inputs. They use no earlier history and keep
budgeting Off. Importing them preserves their saved widget positions. The new
recipes carry current schemas, and tests check all package and core socket types.

See [graph composition](../docs/graph-composition.md),
[explicit messages](../docs/messages.md) and
[caption groups](../docs/captions.md) for the full recipe contracts. Other new
surfaces have focused guides: [router presets](../docs/router-presets.md),
[Request Budget](../docs/request-budget.md),
[profile authoring](../docs/task-profile-authoring.md) and
[audio transcription](../docs/audio-transcription.md). Budgeting defaults to Off
on Generate; the separate Request Budget node defaults to Report and does no
generation. Audio transcription requires the documented installed pair and a
bounded mono 16 kHz clip; the image examples do not enable audio implicitly.

Each canonical JSON workflow has a same-stem 768 by 768 JPEG thumbnail captured
from the current Comfy frontend. Setup Check and Quick Text retain their existing
thumbnails.

On the tested frontend 1.45.20, terminal text remains in native jobs and history
output but is not rendered inline in App Mode's central result pane. The App Mode
example exposes transient read-only Generation Status and Live Response fields
for current-session feedback. They reset on reload and are neither serialized
nor surrogate output files.

The examples are demonstrations, not substitutes for the full
[0.4 user acceptance checklist](../docs/user-acceptance-0.4.md).
