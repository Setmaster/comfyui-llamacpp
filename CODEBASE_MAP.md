<!-- agent-evolve:BEGIN AUTO -->

# CODEBASE_MAP

Generated: 2026-07-12 13:26:38Z
Commit: b822d487c3f035128ac0ee673b94acb7e2d96c6b
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
- `tests/`: 35 files
  - `tests/js/`: 8 files
  - `tests/fixtures/`: 4 files
    - `tests/fixtures/workflows/`: 3 files
- `nodes/`: 20 files
- `changes/`: 19 files
  - `changes/archive/`: 9 files
    - `changes/archive/2026-07-10-sota-refactor/`: 5 files
    - `changes/archive/2026-07-10-lifecycle-concurrency-hardening/`: 4 files
  - `changes/2026-07-11-canonical-generate-work-block-b/`: 5 files
  - `changes/2026-07-11-ux-work-block-a/`: 5 files
- `example_workflows/`: 18 files
- `web/`: 17 files
- `docs/`: 14 files
  - `docs/research/`: 4 files
- `runtime/`: 13 files
- `generation/`: 8 files
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
- `runtime/service.py`: 68.0 KiB
- `docs/research/local-only-comfyui-llm-options-user-guide-2026-07-10.md`: 57.5 KiB
- `tests/test_runtime_service.py`: 54.9 KiB
- `generation/execution.py`: 53.6 KiB
- `tests/test_canonical_generation.py`: 50.6 KiB
- `docs/research/comfyui-llamacpp-vs-llm-party-deep-comparison-2026-07-10.md`: 49.3 KiB
- `docs/research/comfyui-llamacpp-project-frontier-review-2026-07-10.md`: 47.2 KiB

## Hotspots (most lines, sampled from large files)
- `runtime/process.py`: 2838 lines
- `tests/test_process.py`: 2710 lines
- `runtime/service.py`: 1744 lines
- `tests/test_canonical_generation.py`: 1516 lines
- `tests/test_runtime_service.py`: 1502 lines
- `uv.lock`: 1501 lines
- `generation/execution.py`: 1349 lines
- `docs/research/comfyui-llamacpp-vs-llm-party-deep-comparison-2026-07-10.md`: 1061 lines
- `docs/research/comfyui-llamacpp-project-frontier-review-2026-07-10.md`: 976 lines
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
  `changes/2026-07-11-ux-work-block-a/`,
  `changes/archive/2026-07-10-sota-refactor/`, and `docs/research/`.
