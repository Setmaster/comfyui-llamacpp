# Tasks: auto-vlm-projector-resolution

Date: 2026-07-24

## Checklist

- [x] Inspect pinned and current llama.cpp projector behavior.
- [x] Inspect Comfy combo validation, workflow migration, and both renderer
  presentation paths.
- [x] Inspect installed Gemma, Qwen, and MiniCPM GGUF metadata.
- [x] Compare local competitor matching policies.
- [x] Freeze the resolver, text-only, status, and image-diagnostic contract.
- [x] Implement bounded GGUF metadata and tensor-descriptor parsing.
- [x] Implement metadata-first projector resolution and model facade.
- [x] Implement exact direct command/environment behavior and status provenance.
- [x] Implement passive image preflight and safe structured error mapping.
- [x] Update the canonical workflow, migration, documentation, and examples.
- [x] Run focused parser, resolver, config, manager, generation, streaming, JS,
  workflow, and compatibility tests.
- [x] Run full Python and JavaScript suites, Ruff, format, package, extracted
  artifact, and Registry checks.
- [x] Validate auto, explicit, text-only, ambiguity, diagnostics, and release in
  real Windows ComfyUI with the installed model corpus.
- [x] Complete independent final review and resolve findings.
- [x] Review the full diff, commit and push `dev`, update the installed clone,
  document Project KB evidence, and close the native goal.

## Verification commands

- `.venv/bin/python -m pytest -q`
- `.venv/bin/ruff check .`
- `.venv/bin/ruff format --check .`
- `npm test`
- `find web tests/js -type f \( -name '*.js' -o -name '*.mjs' \) -print0 |
  xargs -0 -n1 node --check`
- `uv build`
- `uvx twine check dist/*`
- `git diff --check`
- Real Windows ComfyUI API/history, process, `/props`, and GPU checks recorded
  in `docs/validation-0.4.md`.

## Rollback

- Revert the implementation commits on `dev`.
- Restore the maintained Windows clone to baseline
  `60ae2bfa686298edb80cad8280f1b0092af811c0`.
- `master`, tag `0.3.0`, and the pinned b9957 rollback remain unchanged.
