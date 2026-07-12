# Quickstart: ux-work-block-a

Date: 2026-07-11

## Fast validation

- `uv run python -m pytest -q tests/test_node_contracts.py tests/test_node_metadata.py tests/test_generation.py tests/test_node_helpers.py tests/test_example_workflows.py`
- `node --test tests/js/*.test.mjs`
- `uv run ruff check . && uv run ruff format --check .`
- `git diff --check`

For real UI validation, first checkpoint the product commit, fetch that exact SHA
into the clean installed clone under `C:\\ComfyUI\\custom_nodes`, and launch Comfy
with only `comfyui-llamacpp` whitelisted plus a disposable validation user
directory. Test both renderer settings and prove exact clone HEAD before using the
result as evidence.
