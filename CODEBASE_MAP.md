<!-- agent-evolve:BEGIN AUTO -->

# CODEBASE_MAP

Generated: 2026-07-10 20:34:02Z
Commit: 1e3b7a5a2d90ee3a40cf40e01784de1c343ea85a
Source: git ls-files (tracked files)

## Stack signals
- `pyproject.toml`
- `requirements.txt`

## Key files
- `README.md`

## Directory structure (depth <= 3)
- (root files): 8
- `nodes/`: 10 files
- `web/`: 4 files

## Suggested commands (best-effort)
- `python -m pytest`

## Hotspots (largest text files)
- `server_manager.py`: 30.8 KiB
- `README.md`: 15.9 KiB
- `nodes/advpp_prompt.py`: 13.2 KiB
- `nodes/adv_prompt.py`: 11.4 KiB
- `streaming_client.py`: 10.3 KiB
- `nodes/basic_prompt.py`: 9.0 KiB
- `web/advpp_prompt.js`: 7.8 KiB
- `nodes/model_management.py`: 6.8 KiB
- `model_manager.py`: 6.1 KiB
- `nodes/start_router.py`: 5.0 KiB

## Hotspots (most lines, sampled from large files)
- `server_manager.py`: 877 lines
- `README.md`: 410 lines
- `nodes/advpp_prompt.py`: 371 lines
- `nodes/adv_prompt.py`: 323 lines
- `streaming_client.py`: 299 lines
- `nodes/basic_prompt.py`: 252 lines
- `nodes/model_management.py`: 227 lines
- `model_manager.py`: 191 lines
- `web/advpp_prompt.js`: 183 lines
- `nodes/start_router.py`: 151 lines

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
