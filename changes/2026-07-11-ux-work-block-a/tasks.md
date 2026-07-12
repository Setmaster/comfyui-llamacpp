# Tasks: ux-work-block-a

Date: 2026-07-11

## Checklist

- [x] Release and verify immutable 0.3 baseline.
- [x] Refresh `CODEBASE_MAP.md` and create this change bundle.
- [x] Freeze the complete 0.3 backend schema before presentation edits.
- [x] Add metadata helper, categories, aliases, labels, and advanced sets.
- [x] Add classic-renderer advanced compatibility and dynamic image labels.
- [x] Narrow historical presentation assertions and add current metadata tests.
- [x] Implement template merge, validation, stale guard, explicit replace, and undo.
- [x] Correct existing template defects without expanding the template set.
- [x] Add bounded device probing and idle setup diagnostics to Server Status.
- [x] Move workflows to `example_workflows/`; add Setup Check and Quick Text.
- [x] Capture matching first-run thumbnails and browser evidence in current ComfyUI.
- [x] Add Start Here and update README, troubleshooting, packaging, and test
  documentation.
- [x] Run targeted and full automated validation.
- [x] Run real LiteGraph and Nodes 2.0 browser gates, real generation, and cleanup.
- [x] Obtain independent review and close all P0/P1 findings.
- [x] Review final diff, commit/push `dev`, confirm CI, and update Project KB.

## Verification commands

- `uv run python -m pytest -q`
- `uv run ruff check .`
- `uv run ruff format --check .`
- `node --test tests/js/*.test.mjs`
- `for file in web/*.js; do node --check "$file"; done`
- `uv build`
- `uvx twine check dist/*`
- `uv run python tests/check_distribution.py dist`
- `COMFY_NO_TELEMETRY=1 uvx --from comfy-cli==1.12.0 comfy node validate`
- Inspect wheel and sdist manifests for workflows and thumbnails.
- Real ComfyUI 0.27.0, frontend 1.45.20, LiteGraph, Nodes 2.0, template,
  diagnostics, saved-workflow, and real-generation browser checks.
- `git diff --check` and full `git diff` review.

## Rollback

- Revert Work Block A commits on `dev`. Restore the validation clone to tag
  `0.3.0` or fast-forward it to the last accepted `origin/dev` SHA. Do not move
  `master`, tag `0.3.0`, or Registry version 0.3.0.
