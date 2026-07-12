# Tasks: canonical-generate-work-block-b

Date: 2026-07-11

## Checklist

- [x] Reconcile the accepted Frontier Review and completed Work Block A.
- [x] Refresh `CODEBASE_MAP.md` and initialize this change bundle.
- [x] Complete independent Generate/profile, live/cancel, and
  discovery/release architecture reviews.
- [x] Freeze the compatibility, product, event, discovery, profile, and lifecycle
  contracts in `PLANS.md` and this bundle.
- [x] Add characterization tests for all new typed contracts and retain exact
  legacy schema/behavior fixtures.
- [x] Implement request/result/profile dataclasses and deterministic JSON
  round trips.
- [x] Add canonical payload semantics for Default/Custom sampling and
  Auto/Off/On thinking without changing legacy payloads.
- [x] Add bounded per-user profile loading, GET route, snapshot node, and
  explicit undoable frontend update.
- [x] Add passive props, Known/Unknown discovery, runtime epochs, missing saved
  state, and suggestion-only adjacent projector guidance.
- [x] Add exact managed generation leases, scoped operation queue/worker,
  release handles, global dominance, and full race coverage.
- [x] Add resumable stream capability probing, strict UUID DELETE control,
  prompt progress, one-second pings, and unconditional session cleanup.
- [x] Add execution identities, bounded preview buffers, 8 Hz coalescing,
  active-state restoration, targeted events, and exact/local/global cancellation.
- [x] Add `LlamaCppGenerate` with strict errors, partial policy, JSON syntax
  validation, rich result, and terminal release integration.
- [x] Add classic and Nodes 2.0 frontend controls for sampling, images, model
  discovery, seed, profile, live status, preview, and cancellation.
- [x] Add canonical workflows, App Mode surfaces, thumbnails, Start Here,
  README, lifecycle, troubleshooting, migration, changelog, and validation docs.
- [x] Run focused and complete Python/JavaScript/lint/format/package/security
  gates plus extracted wheel/sdist tests.
- [x] Validate current Comfy classic and Nodes 2.0, including serialization,
  reload, duplicate instances, errors, and App Mode; retain automated execution
  identity coverage for subgraphs and list mapping.
- [x] Validate real direct, VLM, structured, router, exact cancellation, native
  free, scoped direct/router release, driver memory, and downstream allocation.
- [x] Run independent full-diff review and close every P0/P1/P2 finding.
- [x] Review `git diff`, commit/push `dev`, verify final CI, install exact head,
  prove `master`/`0.3.0` unchanged, update Project KB, and complete the goal.

## Verification commands

- `.venv/bin/python -m pytest -q`
- `node --test tests/js/*.test.mjs`
- `.venv/bin/ruff check .`
- `.venv/bin/ruff format --check .`
- `find web tests/js -name '*.js' -o -name '*.mjs' | xargs -n1 node --check`
- `uv build`
- `uvx twine check dist/*`
- `COMFY_NO_TELEMETRY=1 uvx --from comfy-cli==1.12.0 comfy node validate`
- `uvx pip-audit --requirement requirements.txt --progress-spinner off --strict`
- `.venv/bin/python tests/check_distribution.py dist`
- exact extracted wheel/sdist test commands recorded at closeout
- Playwright harness identities, browser evidence, and prompt IDs recorded in
  the validation report and final Project KB recap
- real llama-server/GPU evidence, prompt IDs, and artifacts recorded in the
  validation report and final Project KB recap
- `git diff --check`
- `git status --short --branch`
- `git rev-parse HEAD origin/dev origin/master '0.3.0^{}'`

## Rollback

- Revert the Work Block B commits on `dev`.
- Restore the installed clone to the last accepted `dev` commit
  `7317660cdb0d6c17b4568ca2f80c5d2794699e61`.
- `master` and immutable tag `0.3.0` remain the stable product rollback.
