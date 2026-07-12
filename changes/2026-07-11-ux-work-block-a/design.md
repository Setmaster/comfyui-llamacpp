# Design: ux-work-block-a

Date: 2026-07-11

## Approach

- Add one small Python presentation helper that copies V1 input schemas while
  preserving insertion order, names, types, defaults, and call signatures.
- Keep node display mappings unchanged. Classes opt into one category, alias list,
  and an explicit advanced-field set.
- Add one instance-scoped frontend helper that mirrors backend advanced metadata
  into classic LiteGraph state and resynchronizes only the affected node on link
  changes. Nodes 2.0 continues using native metadata.
- Keep template control logic pure and testable in `template_utils.js`; leave
  `advpp_prompt.js` as the Comfy registration and fetch adapter.
- Keep setup collection separate from runtime lifecycle locks and the existing
  status HTTP route. Reuse cached binary capability probes, add an optional
  bounded device probe, and summarize one catalog scan.
- Use native Comfy workflow JSON and actual browser captures for templates and
  thumbnails.

## Data model changes

- No runtime data model changes.
- Add immutable `v0_3_0_contracts.json` characterization anchored to tag 0.3.0.
- Add presentation-only input `display_name` and `advanced` options.
- Add optional Server Status `binary_path` after the released empty prefix.
- Add setup diagnostics as formatted text plus compact JSON inside the existing
  `info` string and UI payload.

## API / surface changes

- Node categories become nested menu paths. Node IDs and display titles do not
  change.
- Search aliases expose ordinary LLM, GGUF, VLM, prompt, JSON, and VRAM terms.
- ADV++ gains one nonserialized frontend button for explicit template replace.
- Server Status gains one optional binary path widget and visible text output;
  its existing result tuple remains unchanged.
- Workflow templates move from the accepted alias `examples/` to canonical
  `example_workflows/` and grow by two first-run entries.

## Migration notes

- No saved-workflow migration is required. Existing values remain positional and
  old node types remain registered.
- Existing custom node titles remain unchanged because display mappings stay
  fixed.
- Direct GitHub links using `examples/` must be updated to `example_workflows/`.
- The installed Comfy clone is tested at the exact Work Block A commit, then
  restored to tracking `origin/dev` by fast-forward only.

## Risks

- Renderer implementation differences are covered by pure JS tests and live gates
  in LiteGraph plus Nodes 2.0.
- Template async races are blocked by a per-node revision and selected-value check.
- Probe delays are capped and failures become warnings rather than hidden hangs.
- Model/projector counts are informational; compatibility is never inferred.
- UI-only widgets explicitly disable serialization to protect widget arrays.
