# Canonical Generate Validation

This report records the post-0.3 Work Block B validation. It is intentionally
separate from the accepted [0.3 report](validation-0.3.md), whose immutable tag
and evidence remain the rollback baseline.

## Candidate identity

- Branch: `dev`
- Candidate commit: pending final commit
- Package version: `0.4.0` candidate, not published to the Registry
- Stable rollback: tag `0.3.0` at
  `365986af4a47426b5513b3cee917ebec93a4204a`
- Stable branch: `master` at
  `40ff5d730dc27cb51d68ae53142cab9b4c91f5af`

## Automated gates

Pending final candidate execution and exact command output.

Required coverage includes:

- complete Python and JavaScript suites;
- Ruff check and format check;
- JavaScript syntax checks;
- wheel and source archive construction, metadata, manifest, and fresh extracted
  artifact tests;
- Comfy custom-node validation and dependency audit;
- immutable 0.2.1 and complete 0.3 compatibility fixtures;
- request, result, profile, discovery, live event, exact stream cleanup, runtime
  epoch, generation lease, and scoped release race contracts.

## Current Comfy browser gate

Pending final candidate validation in the maintained Windows ComfyUI install.

The gate covers classic and Nodes 2.0 rendering, node creation, serialization,
reload, duplication, dynamic/list execution identity, stale-event handling,
profile refresh and undoable snapshot update, passive missing-model retention,
strict errors, App Mode, and all canonical workflow templates.

## Real llama.cpp and GPU gate

Pending final candidate validation against the maintained current Windows
llama.cpp deployment and RTX 5090.

The gate covers direct text, vision, structured JSON, router exact targeting,
exact generation cancellation, native global free, direct release-after, router
model-scoped release-after, driver-visible convergence, and a subsequent
diffusion allocation without restarting ComfyUI.

## Independent review

Pending fresh-eyes full-diff review and closure of every P0, P1, and P2 finding.

## CI and installed clone

Pending pushed GitHub Actions evidence and proof that
`C:\ComfyUI\custom_nodes\comfyui-llamacpp` is clean at the exact candidate SHA.

## Remaining gaps

Pending completion of the gates above. This document must not be treated as a
release claim until the pending fields are replaced with exact evidence.
