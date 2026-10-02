<!-- agent-evolve:BEGIN AUTO -->

# CODEBASE_MAP

Generated: 2026-10-02 14:37:48Z
Commit: 1baf736909d1053e619c252b0b4439054b231640
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
- `tests/`: 52 files
  - `tests/js/`: 9 files
  - `tests/benchmarks/`: 8 files
    - `tests/benchmarks/task_profiles/`: 8 files
  - `tests/fixtures/`: 5 files
    - `tests/fixtures/workflows/`: 3 files
- `docs/`: 39 files
  - `docs/research/`: 27 files
    - `docs/research/assets/`: 19 files
- `changes/`: 31 files
  - `changes/archive/`: 17 files
    - `changes/archive/2026-07-10-sota-refactor/`: 5 files
    - `changes/archive/2026-07-10-lifecycle-concurrency-hardening/`: 4 files
    - `changes/archive/2026-10-02-october-candidate-hardening/`: 4 files
    - `changes/archive/2026-10-02-remaining-backlog/`: 4 files
  - `changes/2026-07-11-canonical-generate-work-block-b/`: 5 files
  - `changes/2026-07-11-ux-work-block-a/`: 5 files
  - `changes/2026-07-24-auto-vlm-projector-resolution/`: 4 files
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
- `docs/research/assets/2026-10-02-backlog/heldout/results.json`: 180.4 KiB
- `docs/research/assets/2026-10-02-backlog/task-profiles/results.json`: 135.0 KiB
- `runtime/process.py`: 112.7 KiB
- `tests/test_process.py`: 89.5 KiB
- `runtime/service.py`: 68.0 KiB
- `tests/test_canonical_generation.py`: 61.5 KiB
- `tests/test_streaming.py`: 58.7 KiB
- `docs/research/local-only-comfyui-llm-options-user-guide-2026-07-10.md`: 57.5 KiB
- `generation/execution.py`: 56.3 KiB

## Hotspots (most lines, sampled from large files)
- `docs/research/assets/2026-10-02-backlog/heldout/results.json`: 3129 lines
- `runtime/process.py`: 2843 lines
- `docs/research/assets/2026-10-02-backlog/task-profiles/results.json`: 2779 lines
- `tests/test_process.py`: 2723 lines
- `tests/test_canonical_generation.py`: 1824 lines
- `runtime/service.py`: 1744 lines
- `tests/test_streaming.py`: 1643 lines
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
