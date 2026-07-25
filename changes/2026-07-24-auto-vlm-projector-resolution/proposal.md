# Proposal: auto-vlm-projector-resolution

Date: 2026-07-24

> Do not put secrets in this folder. Use 1Password.

## Why

- `StartLlamaCppServer` currently maps `(auto)` to no `--mmproj` argument.
  llama.cpp only performs projector auto-discovery for Hugging Face `-hf`
  downloads, not for this pack's direct local `-m` launch.
- This made a valid image payload fail later with a generic generation error.
  It also left the canonical VLM workflow with an invalid projector placeholder.
- Filename-only or unique-sibling matching is unsafe. The installed `gemma-4`
  folder contains 26B and 31B models with different input widths. One projector
  could be the unique sibling while still being wrong for one model.
- The installed corpus contains enough GGUF metadata to prove modern pairings,
  including lineage, model name, family, effective embedding width, and Qwen
  deepstack structure. Narrow tensor-header checks verify the actual
  Gemma/Qwen output interface and cover the legacy MiniCPM bundle without
  loading tensor data.

## Scope

- Keep one `Vision Projector` selector with `(auto)` first and default,
  `(none - text only)` second, and exact local projector paths after them.
- Add a dependency-free, bounded GGUF v2/v3 header reader and a local,
  metadata-first projector resolver.
- Resolve before `ServerConfig` and manager startup. Pass an exact `--mmproj`
  only for a confident match. Pass `--no-mmproj` for explicit or automatic
  text-only outcomes, and remove inherited projector-specific llama.cpp
  environment variables from the owned child.
- Allow adjacency to contribute local identity evidence, and allow a
  catalog-wide candidate only with strong metadata identity. Assess every
  proven candidate together, group equivalent quantization variants, and
  prefer Q8_0 regardless of which configured root contains it. Fail before
  spawning on ambiguity, incompatible nearby candidates, or a known VLM with no
  match.
- Add passive image-capability preflight and a narrow, non-leaking classifier
  for llama.cpp's structured unsupported-image response.
- Show the effective projector outcome in Server Status, migrate only the
  invalid canonical placeholder, and update examples and documentation.

## Non-goals

- No downloads, network matching, model store, or cloud API behavior.
- No router projector-policy change.
- No automatic substitution for a concrete projector selection.
- No full projector tensor load, trial server launch, or GPU allocation during
  resolution.
- No merge to `master`, Registry publication, or 0.4.0 release.

## Risks

- GGUF is untrusted input. The reader must bound counts, lengths, nesting,
  scanned bytes, and tensor descriptors, and must fail closed.
- Metadata quality varies. Unsupported or weak identities remain manual rather
  than being guessed.
- Existing workflows using `(auto)` intentionally gain real resolution.
  `(none - text only)` is the opt-out.
- A loaded projector changes VRAM use and can affect upstream text-only cache
  behavior. Users can choose explicit text-only mode when vision is not needed.

## Rollback

- Revert this change bundle's product commits on `dev`.
- The immutable 0.3.0 tag and `master` remain untouched.
- Existing explicit projector selections remain valid throughout the change.
