# Example workflows

These workflows are generated and round-tripped through the current ComfyUI
frontend during release validation.

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
`binary_path` when `llama-server` is not on ComfyUI's `PATH`.

The examples are demonstrations, not substitutes for the full
[user acceptance checklist](../docs/user-acceptance.md).
