# Test Suite

## Legacy contract fixtures

`fixtures/workflows/v0_2_1_contracts.json` was derived from release `0.2.1` at
commit `1e3b7a5a2d90ee3a40cf40e01784de1c343ea85a`. It records the public node
IDs, Python module/class locations, Comfy function and output contracts,
historical input order, positional primitive widget order, and defaults.

`fixtures/workflows/v0_2_1_saved_workflow.json` contains one saved node record
for every node available in that release. Its deliberately non-default widget
values make positional shifts visible during refactors. The Prompt Output node
has one additional frontend-only persisted display value, matching the release
frontend extension.

The old required, optional, primitive-widget, and Python-call sequences are
treated as prefixes. New inputs and parameters may be appended, but must not be
inserted into those historical sequences.

## Standalone imports

`conftest.py` provides narrow import-time stubs for ComfyUI and dependencies
that are normally supplied by a Comfy installation. Runtime use of those stubs
raises an assertion. Behavioral tests should inject explicit fakes instead of
expanding these stubs into replacement implementations.

## Run

With pytest installed:

```bash
pytest -q tests
```

Without changing the project environment:

```bash
uvx --from pytest pytest -q tests
```
