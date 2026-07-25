# Spec: auto-vlm-projector-resolution

Date: 2026-07-24

## Requirements

- `Vision Projector` choices are exactly `(auto)`, `(none - text only)`, then
  catalog-relative projector names. Widget and call-parameter positions do not
  change.
- A concrete selection launches that exact contained file and bypasses auto.
- `(none - text only)` guarantees that the owned child receives no inherited
  `LLAMA_ARG_MMPROJ`, `LLAMA_ARG_MMPROJ_URL`, or
  `LLAMA_ARG_MMPROJ_AUTO`, and launches with `--no-mmproj`.
- `(auto)` is local-only and deterministic:
  - one confidently compatible identity selects an exact contained path;
  - equivalent quant variants prefer Q8_0, then BF16, F16, and F32;
  - distinct compatible identities or unresolved plausible candidates fail;
  - a known VLM with no compatible projector fails with an actionable message;
  - a text or unknown model with no candidates starts explicitly text-only.
- Compatibility requires structural agreement plus identity evidence. Dimension
  metadata alone and adjacency alone never authorize a match. The actual
  pinned-runtime Gemma or Qwen output tensor must agree with the declared
  interface.
- Qwen deepstack compares the effective model and projector interfaces, not raw
  projection width. Legacy MiniCPM is accepted only by its guarded family
  metadata and known tensor descriptor.
- An unsupported model family with a strongly plausible local projector
  requires an explicit choice. A text or unknown model with no plausible local
  projector starts text-only.
- Credential-bearing repository URLs are rejected as identity evidence and
  never retained in resolver errors, evidence, or browser-facing status.
- Resolver errors use bounded catalog-relative names, never absolute paths, and
  occur before manager mutation.
- Canonical Generate image requests call passive `/props?autoload=false` after
  exact model admission. Known `vision: false` raises
  `capability_unsupported`; unavailable or unknown facts preserve attached and
  unloaded-router compatibility. Released ADV and ADV++ nodes retain their
  legacy request and error contracts.
- Only the exact bounded llama.cpp structured unsupported-image error maps to
  the fixed capability message. Arbitrary response bodies are never exposed,
  logged, retained, or serialized.
- Server Status reports auto-selected, explicit, explicit text-only, or
  automatic text-only outcome without exposing an absolute projector path to
  browser-facing data.
- The canonical VLM workflow saves `(auto)`. Frontend migration rewrites only
  the exact obsolete `(select an installed projector)` value.

## Scenarios

- Gemma 3, Qwen3-VL, Qwen3.5, and guarded MiniCPM bundles select their local
  projector and complete an image request.
- A root-level model can use a nested projector only when strong name or lineage
  metadata proves the same identity.
- Gemma 4 26B and 31B models in one directory select only a matching-width
  projector. A 26B projector is rejected for 31B.
- Q8_0 and F16 copies of one compatible projector identity resolve to Q8_0.
- Two different compatible projector identities fail with bounded candidates
  and preserve an already-running server.
- Explicit selection remains exact even when metadata is incomplete. llama.cpp
  remains the final authority for a bad manual choice.
- Explicit text-only mode permits text generation and rejects a connected image
  locally with an actionable capability error.
- Malformed GGUF files and hostile HTTP error bodies remain bounded and generic.

## Open questions

- None. The research phase resolved the implementation boundary, confidence
  policy, status behavior, and compatibility migration.
