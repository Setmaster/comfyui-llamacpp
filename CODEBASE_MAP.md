<!-- agent-evolve:BEGIN AUTO -->

# CODEBASE_MAP

Generated: 2026-07-12 03:16:10Z
Commit: 7317660cdb0d6c17b4568ca2f80c5d2794699e61
Source: git ls-files (tracked files)

## Stack signals
- `package.json`
- `pyproject.toml`
- `requirements.txt`
- `uv.lock`
- `.github/workflows`

## Key files
- `README.md`
- `.github/workflows`

## Directory structure (depth <= 3)
- (root files): 17
- `tests/`: 26 files
  - `tests/js/`: 5 files
  - `tests/fixtures/`: 4 files
    - `tests/fixtures/workflows/`: 3 files
- `nodes/`: 18 files
- `changes/`: 14 files
  - `changes/archive/`: 9 files
    - `changes/archive/2026-07-10-sota-refactor/`: 5 files
    - `changes/archive/2026-07-10-lifecycle-concurrency-hardening/`: 4 files
  - `changes/2026-07-11-ux-work-block-a/`: 5 files
- `web/`: 12 files
- `docs/`: 10 files
  - `docs/research/`: 4 files
- `example_workflows/`: 10 files
- `runtime/`: 10 files
- `generation/`: 5 files
- `models/`: 3 files
- `.github/`: 1 files
  - `.github/workflows/`: 1 files

## Suggested commands (best-effort)
- `npm run test`
- `python -m pytest`

## Hotspots (largest text files)
- `uv.lock`: 367.3 KiB
- `runtime/process.py`: 112.4 KiB
- `tests/test_process.py`: 89.0 KiB
- `docs/research/local-only-comfyui-llm-options-user-guide-2026-07-10.md`: 57.5 KiB
- `docs/research/comfyui-llamacpp-vs-llm-party-deep-comparison-2026-07-10.md`: 49.3 KiB
- `docs/research/comfyui-llamacpp-project-frontier-review-2026-07-10.md`: 47.2 KiB
- `tests/fixtures/workflows/v0_3_0_contracts.json`: 43.7 KiB
- `docs/research/comfyui-local-llm-vram-unloading-audit-2026-07-10.md`: 43.6 KiB
- `runtime/manager.py`: 30.2 KiB
- `runtime/client.py`: 28.9 KiB

## Hotspots (most lines, sampled from large files)
- `runtime/process.py`: 2838 lines
- `tests/test_process.py`: 2710 lines
- `tests/fixtures/workflows/v0_3_0_contracts.json`: 1962 lines
- `uv.lock`: 1501 lines
- `docs/research/comfyui-llamacpp-vs-llm-party-deep-comparison-2026-07-10.md`: 1061 lines
- `docs/research/comfyui-llamacpp-project-frontier-review-2026-07-10.md`: 976 lines
- `docs/research/local-only-comfyui-llm-options-user-guide-2026-07-10.md`: 903 lines
- `runtime/client.py`: 849 lines
- `runtime/manager.py`: 818 lines
- `docs/research/comfyui-local-llm-vram-unloading-audit-2026-07-10.md`: 501 lines

<!-- agent-evolve:END AUTO -->

## Manual notes (preserved)

- Public compatibility is centered on the node IDs and positional widget schemas exported from `nodes/` and root `__init__.py`. Saved Comfy workflows store primitive widget values positionally.
- `server_manager.py`, `model_manager.py`, and `streaming_client.py` are public compatibility facades. Their implementation can move, but existing imports must continue to work.
- The target dependency direction is `nodes -> generation/runtime/models`, with pure payload and catalog logic separated from Comfy imports.
- Owned process lifecycle, router HTTP operations, generation leases, native Comfy unload integration, and shutdown all converge on one runtime service.
- Current upstream router load and unload are asynchronous. HTTP acceptance is not a VRAM release barrier, so client operations must poll `/models` to a terminal state.
- `POST /models` downloads a model and `DELETE /models` deletes cache content. Neither endpoint is a compatibility fallback for load or unload.
- Ten optional VLM image inputs must be declared in Python. Frontend JavaScript controls presentation only.
- Research and implementation contract: `PLANS.md`,
  `changes/2026-07-11-ux-work-block-a/`,
  `changes/archive/2026-07-10-sota-refactor/`, and `docs/research/`.
