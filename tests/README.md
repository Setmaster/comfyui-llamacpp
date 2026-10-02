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

`fixtures/workflows/v0_3_0_contracts.json` is anchored to immutable tag `0.3.0`
at commit `365986af4a47426b5513b3cee917ebec93a4204a`. It freezes the complete
17-node schema, including all 0.3 inputs, defaults, output tuples, Python call
order, and primitive widget order. Presentation metadata may be additive, but
the full 0.3 sequence remains an exact compatibility prefix. Current workflow
examples also assert Comfy's serialized seed companion stays immediately after
the released seed value. The historical browser migration inserts `fixed` so a
saved numeric 0.2.1 seed keeps its original non-mutating behavior.

## Standalone imports

`conftest.py` provides narrow import-time stubs for ComfyUI and dependencies
that are normally supplied by a Comfy installation. Runtime use of those stubs
raises an assertion. Behavioral tests should inject explicit fakes instead of
expanding these stubs into replacement implementations.

## Run

Install the locked development environment, including the runtime dependencies
used during test collection:

```bash
uv sync --locked --extra dev
uv run --locked python -m pytest -q tests
node --test tests/js/*.test.mjs
```

Build and verify the shipped artifacts:

```bash
uv build
uv run --locked python tests/check_distribution.py dist
uv run --locked python tests/smoke_distribution.py dist --prove-rejection
```

The manifest check covers runtime Python modules, frontend JavaScript/JSON,
workflow assets, and the source test suite. The smoke check extracts both
artifacts into a temporary directory, imports the wheel and verifies all 19
nodes, then runs the Python and JavaScript suites from the extracted sdist.
Python subprocesses skip site initialization and receive only explicit dependency
directories, so editable checkout hooks and `PYTHONPATH` cannot supply missing
package code. Comfy import-only stubs come from the extracted test harness;
runtime dependencies are real installed packages.

`--prove-rejection` also removes a runtime module and a frontend asset from
temporary wheel copies. The checks must reject both, and the damaged runtime
wheel must fail import even while the source checkout remains installed. These
checks do not launch ComfyUI, llama-server, or GPU workloads; live release
acceptance remains separate.
