# Tasks

- [x] Defer ENG-88 hands-on to Backlog; remove it as a development dependency.
- [x] Record native goal, completion contract and revised sequence.
- [x] ENG-91: implement/test preview notice; verify real graph/App Mode.
- [x] ENG-92: implement/test transfer reduction; measure on installed CUDA runtime.
- [x] ENG-89: prepare fixtures, run fixed bakeoff, score independently and decide.
- [x] ENG-90: pin contract, inventory/probe capability and document ship/defer outcome.
- [x] ENG-94: repair newly observed wrapped timeout classification and prove Windows CI.
- [x] ENG-95: execute frozen two-model held-out check; blind-score and ship/defer.
- [x] Review diffs and independent findings; fix any regressions.
- [x] Run final automated, package, native-Windows and browser gates.
- [x] Commit/push dev, verify CI and synchronize maintained Windows clone.
- [x] Update evidence, Linear/KB, stop owned runtimes and pass completion gate.

## Verification commands

```bash
.venv/bin/python -m pytest -q
node --test tests/js/*.test.mjs
.venv/bin/ruff check .
.venv/bin/ruff format --check .
uv sync --locked --extra dev --dry-run
uv build --out-dir /tmp/comfyui-llamacpp-backlog-release-check
.venv/bin/python tests/check_distribution.py /tmp/comfyui-llamacpp-backlog-release-check
.venv/bin/python tests/smoke_distribution.py /tmp/comfyui-llamacpp-backlog-release-check --prove-rejection
uvx twine check /tmp/comfyui-llamacpp-backlog-release-check/*
git diff --check
```

Native commands, browser fixtures and investigation commands are recorded in
`docs/validation-2026-10-backlog.md` and the linked research reports.

## Outcome

Source and isolated package: 1,141 tests plus 166 subtests. Native Windows: 1,097
plus 166 subtests, with 44 platform skips. Frontend: 79. Seven-job CI and
independent reviews pass. Both profile candidates
are deferred by the fixed quality gates; public audio remains a scoped proposal
after positive short-clip ASR compatibility proof. No release promotion. Detailed
evidence and exact commands: docs/validation-2026-10-backlog.md.
