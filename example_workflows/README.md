# Example workflows

ComfyUI discovers this canonical `example_workflows` directory in **Workflow >
Browse Templates**. Workflows are generated and round-tripped through the
current frontend during validation.

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
The VLM example also records the release-validation input filename. Select an
image from your own ComfyUI input folder before running it.

The examples are demonstrations, not substitutes for the full
[user acceptance checklist](../docs/user-acceptance.md).
