<!-- agent-evolve:BEGIN AUTO -->

# CODEBASE_MAP

Generated: 2026-07-10 21:51:13Z
Commit: e566bd427cf0f1bd9866eeb5645de6bc953b3d49
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
- (root files): 15
- `nodes/`: 17 files
- `tests/`: 17 files
  - `tests/fixtures/`: 3 files
    - `tests/fixtures/workflows/`: 2 files
  - `tests/js/`: 1 files
- `runtime/`: 9 files
- `web/`: 7 files
- `changes/`: 5 files
  - `changes/2026-07-10-sota-refactor/`: 5 files
- `generation/`: 5 files
- `docs/`: 4 files
  - `docs/research/`: 4 files
- `models/`: 3 files
- `.github/`: 1 files
  - `.github/workflows/`: 1 files

## Suggested commands (best-effort)
- `npm run test`
- `python -m pytest`

## Hotspots (largest text files)
- `uv.lock`: 367.3 KiB
- `docs/research/local-only-comfyui-llm-options-user-guide-2026-07-10.md`: 57.5 KiB
- `docs/research/comfyui-llamacpp-vs-llm-party-deep-comparison-2026-07-10.md`: 49.3 KiB
- `docs/research/comfyui-llamacpp-project-frontier-review-2026-07-10.md`: 47.2 KiB
- `docs/research/comfyui-local-llm-vram-unloading-audit-2026-07-10.md`: 43.6 KiB
- `runtime/process.py`: 40.6 KiB
- `runtime/client.py`: 28.3 KiB
- `runtime/service.py`: 24.8 KiB
- `runtime/manager.py`: 22.4 KiB
- `README.md`: 15.9 KiB

## Hotspots (most lines, sampled from large files)
- `uv.lock`: 1501 lines
- `runtime/process.py`: 1093 lines
- `docs/research/comfyui-llamacpp-vs-llm-party-deep-comparison-2026-07-10.md`: 1061 lines
- `docs/research/comfyui-llamacpp-project-frontier-review-2026-07-10.md`: 976 lines
- `docs/research/local-only-comfyui-llm-options-user-guide-2026-07-10.md`: 903 lines
- `runtime/client.py`: 833 lines
- `runtime/service.py`: 716 lines
- `runtime/manager.py`: 593 lines
- `docs/research/comfyui-local-llm-vram-unloading-audit-2026-07-10.md`: 501 lines
- `README.md`: 410 lines

<!-- agent-evolve:END AUTO -->

## Manual notes (preserved)

- Public compatibility is centered on the node IDs and positional widget schemas exported from `nodes/` and root `__init__.py`. Saved Comfy workflows store primitive widget values positionally.
- `server_manager.py`, `model_manager.py`, and `streaming_client.py` are public compatibility facades. Their implementation can move, but existing imports must continue to work.
- The target dependency direction is `nodes -> generation/runtime/models`, with pure payload and catalog logic separated from Comfy imports.
- Owned process lifecycle, router HTTP operations, generation leases, native Comfy unload integration, and shutdown all converge on one runtime service.
- Current upstream router load and unload are asynchronous. HTTP acceptance is not a VRAM release barrier, so client operations must poll `/models` to a terminal state.
- `POST /models` downloads a model and `DELETE /models` deletes cache content. Neither endpoint is a compatibility fallback for load or unload.
- Ten optional VLM image inputs must be declared in Python. Frontend JavaScript controls presentation only.
- Research and implementation contract: `PLANS.md`, `changes/2026-07-10-sota-refactor/`, and `docs/research/`.
