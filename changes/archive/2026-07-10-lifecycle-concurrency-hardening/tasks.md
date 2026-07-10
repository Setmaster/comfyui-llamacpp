# Tasks: lifecycle-concurrency-hardening

Date: 2026-07-10

## Checklist

- [x] Make runtime release and generation lease transitions operation-authoritative.
- [x] Serialize explicit manager lifecycle/router operations and reject them before mutation during generation.
- [x] Normalize bind/connect/managed endpoint handling and validate unsupported hosts.
- [x] Add Linux parent-death supervisor and correct Windows fallback diagnostics.
- [x] Parse current build output and gate symbolic GPU layers from real help shapes.
- [x] Reject typed/reserved aliases in `extra_args`.
- [x] Treat router terminal failed-unload state as released while preserving diagnostics.
- [x] Add blocking concurrency, endpoint, capability, and real subprocess regressions.
- [x] Run focused and full tests, Ruff, formatting checks, and review the complete diff.

## Verification commands

- `PYTHONPATH=. .venv/bin/pytest -q tests/test_runtime_service.py tests/test_manager.py tests/test_process.py tests/test_config.py`
- `PYTHONPATH=. .venv/bin/pytest -q`
- `.venv/bin/ruff check .`
- `.venv/bin/ruff format --check .`
- `git diff --check`
- `.venv/bin/python -m build --wheel --outdir /tmp/comfyui-llamacpp-lifecycle-wheel .`

## Rollback

- Revert the files listed in the lifecycle-concurrency change bundle; no schema or persistent state rollback is needed.
