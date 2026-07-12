# Example workflows

ComfyUI discovers this canonical `example_workflows` directory in **Workflow >
Browse Templates**. Workflows are generated and round-tripped through the
current frontend during validation.

- `canonical-text.json`: canonical text generation with a portable Freeform
  profile snapshot, strict errors, live status, and terminal managed release.
- `canonical-vlm-image-understanding.json`: connect one Comfy image to canonical
  Generate for factual local image understanding.
- `canonical-structured-json.json`: connect a strict JSON Schema and validate the
  terminal response as JSON.
- `canonical-app-mode.json`: a small App Mode surface exposing selected local
  model and generation controls plus transient live status and response, with
  Generate itself as the native output-history node.
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
The canonical text examples leave projector selection on `(auto)`, which sends
no explicit projector. The VLM example uses a visible projector placeholder so
it cannot silently pair one; select and confirm the matching projector plus an
image from your own ComfyUI input folder before running it.

The four `canonical-*.json` examples target 0.4.0. Their saved Freeform profile
is a portable no-op snapshot, so execution never depends on a mutable profile
file from the machine that created the workflow. The other seven examples remain
the exact 0.3.0 workflow assets.

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
