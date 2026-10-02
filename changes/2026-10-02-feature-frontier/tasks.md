# Tasks: feature-frontier

Date: 2026-10-02

## Checklist

- [x] Reconcile research, authorization, baseline and native goal.
- [x] Record completion contract and Linear execution state.
- [ ] ENG-99 result inspector and native composition proof.
- [ ] ENG-100 presets and native router proof.
- [ ] ENG-101 explicit messages and round-trip/native proof.
- [ ] ENG-102 context budgets and exact endpoint/resource proof.
- [ ] ENG-103 caption coordination and failure/cancel cleanup proof.
- [ ] ENG-104 profile authoring and real browser proof.
- [ ] ENG-105 bounded AUDIO adapter and real Comfy/model proof.
- [ ] Independent review, full/package/native/browser acceptance.
- [ ] Push/synchronize dev and Windows clone; verify CI; close Linear with evidence.
- [ ] Archive bundle, update/sync KB, stop owned runtimes and complete native goal.

## Verification commands

```bash
.venv/bin/python -m pytest -q
node --test tests/js/*.test.mjs
.venv/bin/ruff check .
.venv/bin/ruff format --check .
uv sync --locked --extra dev --dry-run
uv build --out-dir /tmp/comfyui-llamacpp-feature-release-check
.venv/bin/python tests/check_distribution.py /tmp/comfyui-llamacpp-feature-release-check
.venv/bin/python tests/smoke_distribution.py /tmp/comfyui-llamacpp-feature-release-check --prove-rejection
uvx twine check /tmp/comfyui-llamacpp-feature-release-check/*
COMFY_NO_TELEMETRY=1 uvx --from comfy-cli==1.12.0 comfy node validate
git diff --check
```

Run focused tests first. Native checks use installed Windows Comfy Python and
b9957 with disposable user/database/output state, plus one stable Playwright
session. Record exact commands and artifacts in the validation report.

## Rollback

Revert focused implementation commits or restore the pre-phase dev baseline
3945082. Preserve user files and the installed model inventory.
