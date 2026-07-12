# Quickstart: canonical-generate-work-block-b

Date: 2026-07-11

## Fast validation

Run the focused contract and runtime set first:

```bash
PYTHONPATH=. .venv/bin/pytest -q \
  tests/test_generation.py \
  tests/test_streaming.py \
  tests/test_client_contract.py \
  tests/test_runtime_service.py \
  tests/test_manager.py \
  tests/test_comfy_bridge.py \
  tests/test_node_contracts.py \
  tests/test_v0_3_contracts.py
```

Run frontend tests:

```bash
node --test tests/js/*.test.mjs
```

Then run the full local gate:

```bash
PYTHONPATH=. .venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
find web tests/js -type f \\( -name '*.js' -o -name '*.mjs' \\) -print0 |
  xargs -0 -n1 node --check
```

Browser and real-runtime validation are mandatory before completion. Their exact
commands, current Comfy/llama.cpp versions, workflow fixtures, process/model
terminal evidence, driver-memory measurements, and downstream allocation proof
must be appended here or to the final validation document.
