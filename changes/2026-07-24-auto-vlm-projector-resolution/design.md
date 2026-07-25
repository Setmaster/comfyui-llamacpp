# Design: auto-vlm-projector-resolution

Date: 2026-07-24

## Approach

1. Read only bounded GGUF metadata and selected tensor descriptors. Cache facts
   by resolved identity and file stat.
2. Resolve the selected model from one containment-safe catalog snapshot.
3. Let adjacent projectors use tightly normalized local identity evidence.
   Admit catalog-wide candidates only through exact normalized metadata name
   or bounded base lineage. Neither location outranks the other after proof.
4. Reject explicit family, vision, dimension, deepstack, and actual output
   tensor conflicts. Group every compatible file by semantic identity, then
   rank equivalent quantizations across the complete group.
5. Return a typed resolution with mode, outcome, relative projector name,
   lexical launch path, and bounded evidence.
6. Build an immutable `ServerConfig` from the resolved path. Resolution never
   occurs inside the manager.
7. Keep projector provenance as manager status metadata, separate from the
   command fingerprint. Add a command-affecting text-only flag so equivalent
   auto and explicit selections reuse the same runtime.
8. Preflight image capability through passive props, then retain a narrow
   structured-error fallback for servers whose capability was unknown.

## Data model changes

- `models.gguf_metadata` exposes bounded model/projector facts and selected
  tensor shapes. It has no third-party dependency.
- `models.projectors` exposes selection constants, assessments, typed
  resolution, and resolver errors.
- `ServerConfig` gains a direct text-only launch flag. It emits
  `--no-mmproj` only when that flag is set.
- The manager retains a small public projector-status snapshot for the active
  direct runtime. It is not part of the command fingerprint.
- Streaming failures gain an internal `image_unsupported` token. Raw bodies do
  not cross the HTTP boundary.

## API / surface changes

- The existing `mmproj` combo gains one choice but keeps its location and
  default.
- `(auto)` changes from "omit projector" to "resolve a confident local match."
- New `(none - text only)` preserves intentional no-projector use.
- Server Status adds one projector outcome line and updates Setup Check wording.
- Canonical Generate raises the existing `CAPABILITY_UNSUPPORTED` category for
  known image incompatibility.

## Migration notes

- Released 0.2.1 and 0.3 positional contracts remain exact.
- Existing valid `(auto)` values are not rewritten. Their behavior improves.
- Existing concrete projector values are not rewritten.
- Only the unreleased canonical VLM placeholder is migrated to `(auto)`.
- Legacy Python callers that construct `ServerConfig` without the new text-only
  flag retain their existing omission behavior.

## Risks

- Metadata producers are inconsistent. Unsupported facts become Unknown and
  require manual selection instead of fallback guessing.
- Global matching is limited to strong identity evidence to prevent unrelated
  same-width models from pairing.
- File replacement during parsing is detected by before/after stat checks.
- Child environment scrubbing is limited to the three projector variables and
  retains CUDA, PATH, authentication, and other inherited settings.
- Status provenance can change from auto to explicit while reusing the same
  exact process. This describes the latest request, not a different binary
  state.
