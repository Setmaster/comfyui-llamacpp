# Tasks: sota-refactor

Date: 2026-07-10

## Checklist

- [x] Wave 0: create and publish `dev`; collect current ComfyUI, frontend, llama.cpp, lifecycle, API, and architecture audits.
- [ ] Wave 1: lock compatibility contracts, historical workflow fixtures, baseline tests, lint, and package scaffolding.
- [ ] Wave 2: extract pure configuration, capability, model-catalog, generation, image, template, and streaming primitives.
- [ ] Wave 3: implement owned POSIX and Windows process trees, continuous log draining, lifecycle states, diagnostics, and deterministic stop barriers.
- [ ] Wave 4: implement the typed llama-server client, authentication, exact router identities, terminal load/unload polling, tokenize, props, and structured errors.
- [ ] Wave 5: add runtime generation leases, coalesced release, Comfy free middleware, namespaced status/release routes, lifecycle events, and optional reverse eviction.
- [ ] Wave 6: reconcile and refactor all prompt, model, token, structured-output, status, start, stop, and router nodes behind compatibility schemas.
- [ ] Wave 7: consolidate dynamic-image, template, and output-preview frontend code without core or prototype hijacking.
- [ ] Wave 8: update versioning, dependencies, LICENSE, README, examples, troubleshooting, migration guidance, CI, and Registry-oriented metadata.
- [ ] Wave 9: run Python, JavaScript, process-tree, fake-server, package, current Comfy, Windows, llama-server, and available GPU handoff validation.
- [ ] Wave 10: independent fresh-eyes review, close all material gaps, archive this bundle, review the full diff, commit and push `dev`, and prepare the user-test handoff.

## Verification commands

- `python -m pytest -q`
- `python -m ruff check .`
- `python -m ruff format --check .`
- `node --test tests/js/*.test.mjs`
- `node --check web/*.js`
- `python -m build`
- `git diff --check`
- `git status --short --branch`
- Current ComfyUI import and workflow fixture smoke commands, recorded once the harness exists.
- Live direct/router llama-server and Windows GPU handoff commands, recorded with exact artifacts during Wave 9.

## Rollback

- Revert the affected reviewable wave commit, or abandon `dev`; `master` is not modified.
- Internal modules are introduced behind compatibility facades so a failed extraction can be reverted without changing public imports.
