<!-- agent-evolve:BEGIN AUTO -->

# CODEBASE_MAP

Generated: 2026-07-10 23:44:55Z
Commit: 1f01fc1d7cfc4ff5d12d3d256ddd5d14a4d313d0
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
- (root files): 16
- `tests/`: 20 files
  - `tests/fixtures/`: 3 files
    - `tests/fixtures/workflows/`: 2 files
  - `tests/js/`: 2 files
- `nodes/`: 17 files
- `runtime/`: 10 files
- `changes/`: 9 files
  - `changes/archive/`: 9 files
    - `changes/archive/2026-07-10-sota-refactor/`: 5 files
    - `changes/archive/2026-07-10-lifecycle-concurrency-hardening/`: 4 files
- `docs/`: 9 files
  - `docs/research/`: 4 files
- `web/`: 8 files
- `examples/`: 6 files
- `generation/`: 5 files
- `models/`: 3 files
- `.github/`: 1 files
  - `.github/workflows/`: 1 files

## Suggested commands (best-effort)
- `npm run test`
- `python -m pytest`

## Hotspots (largest text files)
- `uv.lock`: 367.3 KiB
- `docs/research/local-only-comfyui-llm-options-user-guide-2026-07-10.md`: 57.5 KiB
- `runtime/process.py`: 57.0 KiB
- `docs/research/comfyui-llamacpp-vs-llm-party-deep-comparison-2026-07-10.md`: 49.3 KiB
- `docs/research/comfyui-llamacpp-project-frontier-review-2026-07-10.md`: 47.2 KiB
- `docs/research/comfyui-local-llm-vram-unloading-audit-2026-07-10.md`: 43.6 KiB
- `runtime/manager.py`: 30.2 KiB
- `runtime/client.py`: 28.9 KiB
- `runtime/service.py`: 28.7 KiB
- `tests/test_manager.py`: 23.7 KiB

## Hotspots (most lines, sampled from large files)
- `uv.lock`: 1501 lines
- `runtime/process.py`: 1481 lines
- `docs/research/comfyui-llamacpp-vs-llm-party-deep-comparison-2026-07-10.md`: 1061 lines
- `docs/research/comfyui-llamacpp-project-frontier-review-2026-07-10.md`: 976 lines
- `docs/research/local-only-comfyui-llm-options-user-guide-2026-07-10.md`: 903 lines
- `runtime/client.py`: 849 lines
- `runtime/manager.py`: 818 lines
- `runtime/service.py`: 795 lines
- `tests/test_manager.py`: 667 lines
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
  `changes/archive/2026-07-10-sota-refactor/`, and `docs/research/`.
