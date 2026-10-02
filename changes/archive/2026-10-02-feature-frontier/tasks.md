# Tasks: feature-frontier

Date: 2026-10-02

## Checklist

- [x] Reconcile research, authorization, baseline and native goal.
- [x] Record completion contract and Linear execution state.
- [x] ENG-99 result inspector and native composition proof.
- [x] ENG-100 presets and native router proof.
- [x] ENG-101 explicit messages and round-trip/native proof.
- [x] ENG-102 context budgets and exact endpoint/resource proof.
- [x] ENG-103 caption coordination and failure/cancel cleanup proof.
- [x] ENG-104 profile authoring and real browser proof.
- [x] ENG-105 bounded AUDIO adapter and real Comfy/model proof.
- [x] ENG-106 response deadlines/Windows cancellation and ENG-107 pinned Stop compatibility.
- [x] Independent review, full/package/native/browser acceptance.
- [x] Archive the implementation bundle and stop owned test runtimes.

## Post-commit delivery gate

The private `feature-implementation/final-delivery.json` receipt and Linear own
the exact delivery revision: push/synchronize dev and the Windows clone, read
back CI, compare packaged content, post issue evidence before Done, and sync the
Project KB. Complete the native goal only after those readbacks pass. This
archived implementation checklist does not predeclare that later goal update.

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
