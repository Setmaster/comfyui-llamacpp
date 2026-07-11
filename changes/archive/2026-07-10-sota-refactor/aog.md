# Adaptive Objective Graph: SOTA Refactor

Date: 2026-07-10
Status: Complete

## Stable objective

Deliver a remote `dev` branch containing a current, safe, compatibility-preserving, fully tested `comfyui-llamacpp` refactor that the user can validate before any merge to `master`.

## Mutable path

```text
research evidence
  -> compatibility characterization
  -> pure shared foundations
  -> owned lifecycle + protocol barriers
  -> Comfy two-sided VRAM handoff
  -> node and frontend consolidation
  -> release polish
  -> live validation
  -> independent completion gate
```

Parallel work is allowed after compatibility invariants and module ownership are fixed. Collision files such as root `__init__.py`, `nodes/__init__.py`, `pyproject.toml`, README, and final integration remain coordinator-owned.

## Evidence nodes

- E1: completed frontier review and user-oriented alternatives research.
- E2: current Comfy lifecycle and unload audit.
- E3: current llama.cpp CLI, router, auth, multimodal, structured-output, and idle-sleep audit.
- E4: complete current code and dirty-bundle architecture audit.
- E5: historical workflow and node contract fixtures.
- E6: automated unit, integration, process-tree, and frontend tests.
- E7: package and clean Comfy import checks.
- E8: live current llama-server direct/router checks.
- E9: Windows Comfy and GPU handoff evidence.
- E10: independent final diff and compatibility review.

## Decision gates

- If an extraction changes a legacy contract, stop and repair the compatibility facade before continuing.
- If Comfy middleware registration is unavailable, preserve explicit lifecycle nodes and expose a clear degraded capability instead of blocking import.
- If an optional llama-server flag is absent, omit it and report capability state instead of guessing by version.
- If Windows Job Object ownership cannot be proven, report degraded ownership and use validated descendants; never fall back to a name sweep.
- If a live external dependency cannot be obtained safely, complete fake integration coverage and record the exact live gap without claiming it passed.
- Any merge to `master` remains outside this objective and requires the user's later blessing.

## Completion gate

All requirements in `spec.md` are either proven by named evidence or explicitly documented as an unavoidable environment gap; no known P0 or P1 product defect remains; the full diff is reviewed; all commits are on and pushed to `origin/dev`; and `master` remains unchanged.

Completed at code revision `1f01fc1`. Evidence and remaining platform
boundaries are recorded in `docs/validation-0.3.md`.
