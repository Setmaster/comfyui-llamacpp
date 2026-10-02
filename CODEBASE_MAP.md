<!-- agent-evolve:BEGIN AUTO -->

# CODEBASE_MAP

Generated: 2026-10-02 05:21:13Z
Commit: ec0e7b55ff550554dcacd022cd7c68df68798458
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
- (root files): 18
- `tests/`: 42 files
  - `tests/js/`: 9 files
  - `tests/fixtures/`: 5 files
    - `tests/fixtures/workflows/`: 3 files
- `changes/`: 27 files
  - `changes/archive/`: 13 files
    - `changes/archive/2026-07-10-sota-refactor/`: 5 files
    - `changes/archive/2026-07-10-lifecycle-concurrency-hardening/`: 4 files
    - `changes/archive/2026-10-02-october-candidate-hardening/`: 4 files
  - `changes/2026-07-11-canonical-generate-work-block-b/`: 5 files
  - `changes/2026-07-11-ux-work-block-a/`: 5 files
  - `changes/2026-07-24-auto-vlm-projector-resolution/`: 4 files
- `docs/`: 22 files
  - `docs/research/`: 11 files
    - `docs/research/assets/`: 6 files
- `nodes/`: 20 files
- `example_workflows/`: 18 files
- `web/`: 17 files
- `runtime/`: 14 files
- `generation/`: 8 files
- `models/`: 5 files
- `.github/`: 1 files
  - `.github/workflows/`: 1 files

## Suggested commands (best-effort)
- `npm run test`
- `python -m pytest`

## Hotspots (largest text files)
- `uv.lock`: 367.3 KiB
- `runtime/process.py`: 112.7 KiB
- `tests/test_process.py`: 89.5 KiB
- `runtime/service.py`: 68.0 KiB
- `tests/test_canonical_generation.py`: 61.5 KiB
- `docs/research/local-only-comfyui-llm-options-user-guide-2026-07-10.md`: 57.5 KiB
- `generation/execution.py`: 56.3 KiB
- `tests/test_runtime_service.py`: 54.9 KiB
- `tests/test_streaming.py`: 53.7 KiB
- `tests/test_manager.py`: 53.2 KiB

## Hotspots (most lines, sampled from large files)
- `runtime/process.py`: 2843 lines
- `tests/test_process.py`: 2723 lines
- `tests/test_canonical_generation.py`: 1824 lines
- `runtime/service.py`: 1744 lines
- `tests/test_streaming.py`: 1538 lines
- `tests/test_manager.py`: 1504 lines
- `tests/test_runtime_service.py`: 1502 lines
- `uv.lock`: 1501 lines
- `generation/execution.py`: 1406 lines
- `docs/research/local-only-comfyui-llm-options-user-guide-2026-07-10.md`: 903 lines

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
  `changes/2026-07-11-canonical-generate-work-block-b/`,
  `changes/2026-07-11-ux-work-block-a/`,
  `changes/archive/2026-07-10-sota-refactor/`, and `docs/research/`.
