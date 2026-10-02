# ComfyUI llama.cpp Project Frontier Review, 2026-10-02

## Purpose and method

Review the project end to end, establish its actual state, and prioritize repairs
and expansion. The maintainer explicitly authorized implementation after review
and tracking in the [7dev Linear project](https://linear.app/7dev/project/comfyui-llamacpp-b846a629b382).
This report records the baseline findings. Subsequent implementation evidence
belongs in `docs/validation-2026-10.md`, `PLANS.md`, and the linked issues.

Baseline: clean `dev` at `2d1d5ced6355bdb49e91f72b6ec9b89e93c087d2`.
Three independent read-only reviews covered runtime/models, generation/UI, and
delivery/product documentation. The parent inspected the findings, ran the full
automated baseline and package checks, checked remote and Registry state, and
launched current Windows ComfyUI with only this custom node enabled.

External research was needed for llama.cpp option semantics and Gemma's mixed
text/vision architecture. Primary sources are linked beside those findings.
Existing July evidence was read as historical evidence, not rerun evidence.

## Current state

| Surface | Observed state |
| --- | --- |
| Stable | 0.3.0, immutable tag `365986a`; `master`/remote `master` at `40ff5d7` |
| Candidate | 0.4.0 on `dev`; 19 nodes, including canonical Generate and Task Profile |
| Registry | 0.3.0 is `NodeVersionStatusActive`; 0.4 is unpublished |
| Python baseline | 912 tests and 66 subtests pass |
| Frontend baseline | 59 Node tests pass |
| Quality/package | Ruff, formatting, wheel/sdist build, manifest checker and Twine pass |
| Existing CI | Seven successful jobs at baseline, run `30146091224`, July 25 |
| Current Windows host | ComfyUI 0.37.0 (`8d534945`), frontend 1.53.6, Python 3.13.7 |
| Inference host | llama.cpp b9957 (`c4ae9a88f`), Torch 2.9.1+cu130, RTX 5090 |
| Current browser baseline | 19 nodes register; template browser lists 11 workflows; offline Generate reproduces stuck `Starting...` |
| Promotion | Maintainer hands-on acceptance remains required |

The implementation is a substantial, tested candidate, not an empty prototype.
Its strongest parts are explicit external process ownership, Linux pidfd/Windows
Job lifecycle handling, exact router identities, release barriers, bounded GGUF
inspection, fail-closed projector ambiguity, portable profiles, and compatibility
fixtures for released node/widget contracts. Preserve these boundaries.

The code is concentrated in several large runtime/execution modules. Size alone
does not justify a rewrite: the concrete defects below are separable changes.
Legacy and canonical generation behavior intentionally differ in error handling;
changing released nodes to strict canonical semantics would break compatibility.

## Ranked findings

### P1.1: Alternate option spelling bypasses launch guards and argv redaction

Observed: `runtime/config.py:84` and `runtime/process.py:217` compare option names
without normalizing underscores. Previously resolved reserved-option protection
has a confirmed gap. `--gpu_layers`, `--ctx_size`, `--models_dir` and `--api_key`
are accepted after typed options. A fixture key passed as `--api_key value`
survives argv redaction and can reach status command diagnostics.

[llama.cpp b9957 argument parsing](https://github.com/ggml-org/llama.cpp/blob/b9957/common/arg.cpp#L638)
normalizes underscores to hyphens. This finding does not establish any historical
credential leak; it proves a reachable failure with an inert fixture value.

Fix and proof: canonicalize supported option names for validation/redaction,
without rewriting values; test separate/equals, hyphen/underscore/mixed forms in
direct and router config plus process diagnostics. Tracking: **ENG-79**.

### P2.1: Invalid replacement endpoint tears down the healthy runtime

Observed: `runtime/manager.py:704` stops the owned server before checking whether
the new port is occupied. A fake-process reproduction starting on 18080 then
requesting occupied 18081 returns failure with one stop call and no current config.

Impact: a preflight-detectable replacement error unnecessarily disrupts working
workflows. Fix: test destinations that cannot belong to the current listener
before teardown, retain the post-stop race check, and account for bind overlap.
Proof: preserve PID/config/epoch on rejection; same-endpoint restart still works.
Tracking: **ENG-80**.

### P2.2: Auto rejects supported text-only Gemma 3 variants

Observed: `models/projectors.py:648` treats all `gemma3` metadata as vision.
The 270M and 1B models are text-only in [Google's model table](https://ai.google.dev/gemma/docs/get_started),
but [b9957 conversion](https://github.com/ggml-org/llama.cpp/blob/b9957/conversion/gemma.py#L120)
maps `Gemma3ForCausalLM` to the same GGUF architecture. A 1B metadata fixture
reproduces the missing-projector rejection. Explicit text-only mode is a workaround.

Fix and proof: recognize verified text-only variants from sufficient metadata,
not filenames alone; retain missing/ambiguous-projector rejection for actual VLMs
and conflicting or insufficient evidence. Weight-level testing is separate from
metadata fixture proof and is not claimed here. Tracking: **ENG-81**.

### P2.3: HTTP response trickling exceeds absolute deadlines

Observed: `runtime/client.py:104` checks time after a 64 KiB iterator chunk
yields. A real loopback server sending a 15-byte health body at 35 ms/byte made
a 100 ms request fail only after 503 ms. Socket inactivity timeouts do not bound
continuous delivery, so longer bodies can hold lifecycle operations much longer.

Fix and proof: enforce the absolute deadline throughout header/body transport,
close resources on timeout, and test real trickled success/error bodies and
headers while preserving bounded reads, UTF-8 and safe errors. Tracking: **ENG-82**.

### P2.4: Exact nested direct-model selections are labelled Missing

Observed: `runtime/discovery.py:618` checks absolute and basename identities
before calculating catalog-relative identity; descriptors expose no safe aliases.
With the exact `bundle/model.gguf` running, its saved selection reports Missing.

Fix and proof: resolve proven catalog-relative identity first, expose safe
aliases, retain distinct files with the same basename and path privacy, and test
both direct discovery and automatic frontend refresh. Tracking: **ENG-83**.

### P2.5: Live feedback becomes stale on early failure or delayed Stop replies

Observed: `web/generate.js:273` applies Stop results without checking that the
same execution is still displayed. Stop(A), new execution B, then A's delayed
404 overwrites B's status and disables B's Stop. A delayed success overwrites a
terminal snapshot too.

Separately, `generation/execution.py:992` registers live execution after
preflight. Offline runtime produces no live events, leaving the frontend's
`Starting...` state. The parent reproduced that exact symptom in current Windows
ComfyUI and captured [the failure screenshot](assets/2026-10-02-frontier-review/baseline-offline-status.png).
Image conversion can fail even earlier.

Fix and proof: retain prompt/node/workflow/client isolation; cover early errors
with terminal state and ignore stale cancellation replies. Test the actual
extension with deferred responses and verify the offline error in both current
renderers. Tracking: **ENG-84**.

### P2.6: First-run instructions mix stable and unreleased features

Observed: `docs/start-here.md:9` installs stable 0.3, then requires automatic
projector behavior, Setup Check and Quick Text added after 0.3. README sends
newcomers into that path. The example and 0.4 validation docs call seven files
immutable 0.3 assets, but stable shipped five; router-text was adapted and
Setup Check/Quick Text were added later.

Fix and proof: distinguish stable and candidate steps, correct workflow
provenance against the immutable tag, retain compatibility fixtures, and reread
all edited text. Tracking: **ENG-85**.

### P2.7: Artifact behavior checks remain manual

Observed: CI builds and checks workflow/test member lists but never imports the
isolated wheel or executes the extracted sdist. Source tests import the checkout.
Missing runtime modules or JS assets can therefore escape those checks. Current
baseline packages pass; this is a verification gap, not a broken-package claim.

Fix and proof: automate isolated 19-node registration, runtime/web assets and
extracted Python/JS tests; demonstrate rejection of a damaged artifact. Also fix
the P3 delivery hygiene gaps: enforce `uv sync --locked --extra dev` and remove
pytest-only instructions that omit collection-time Pillow. Tracking: **ENG-86**.

### P2.8: Refresh live acceptance before promoting the candidate

July validation used ComfyUI 0.27/frontend 1.45.20. The installed host is now
0.37/frontend 1.53.6. Current import success alone does not establish generation,
Stop, App Mode, router or VRAM-handoff compatibility.

Proof required: exact installed source, both renderers, real direct/structured/VLM/
router/cancellation/release workflows, owned process and GPU-memory return, full
automated/package gates, independent review, pushed CI and clean installed clone.
Record unavailable surfaces rather than silently substituting unit tests.
Tracking: **ENG-87**, followed by human gate **ENG-88**.

## Next work, parked expansion and non-goals

| Order | Issues | Scope |
| --- | --- | --- |
| 1 | ENG-78 | Complete this review and tracking |
| 2 | ENG-79 | Close the launch guard/redaction defect first |
| 3 | ENG-80 to ENG-84 | Bounded runtime/model/UI correctness repairs; independent files may proceed concurrently |
| 4 | ENG-85, ENG-86 | Correct onboarding and automate delivery proof |
| 5 | ENG-87 | Integrate, independently review, run current runtime gates and hand off dev |
| 6 | ENG-88 | Stop for maintainer acceptance before any promotion/publication |
| Backlog | ENG-89, ENG-90 | Fixed task-profile bakeoff and exact audio compatibility investigation after acceptance |
| Backlog | ENG-91, ENG-92 | Preview-tail indicator and measured reduction of duplicate image CPU transfers |

The preview already carries truncation metadata but omits a user-visible tail
indicator. Image counting and encoding both call `_as_numpy`, potentially
duplicating GPU-to-CPU transfer; first-frame mode also converts the full batch.
These P3 opportunities are source-supported; no performance gain is measured yet.

Do not expand into agents, RAG, MCP, provider aggregation, downloads, persistent
sessions, video, prompt studio or broad V3 migration during this work block.
Do not rewrite mature ownership machinery merely because its module is large.
No new runtime dependency is needed for the selected repairs.

## Evidence and rollback

Baseline commands:

```bash
.venv/bin/python -m pytest -q
npm test
.venv/bin/ruff check .
.venv/bin/ruff format --check .
uv sync --locked --extra dev --dry-run
uv build --out-dir /tmp/comfyui-llamacpp-review-baseline-dist
.venv/bin/python tests/check_distribution.py /tmp/comfyui-llamacpp-review-baseline-dist
uvx twine check /tmp/comfyui-llamacpp-review-baseline-dist/*
git ls-remote --heads origin dev master
gh run list --branch dev --limit 3 --json databaseId,headSha,conclusion,status,createdAt
```

Additional proof: immutable-tag workflow inspection and byte comparisons;
disposable metadata/catalog/fake-runtime reproductions; real loopback HTTP
trickle reproduction; actual JS-extension cancellation harness; current Comfy
`/system_stats`, `/object_info`, template browser and offline Generate failure.
The [Registry version endpoint](https://api.comfy.org/nodes/comfyui-llamacpp/versions/0.3.0)
reports Active without incrementing the install/download counter.

The baseline failure screenshot is preserved under
`docs/research/assets/2026-10-02-frontier-review/`. Additional private browser
evidence is under `/tmp/comfyui-llamacpp-browser-review`.
The parent reviewed source and diffs. Implementation has its own final diff gate.

Rollback: revert focused dev commits or restore the pre-review `2d1d5ce` candidate;
the stable fallback remains immutable 0.3.0. Preserve the host's llama.cpp b9957
installation. Do not reset user edits or alter unrelated Comfy custom nodes.

Review limitation: fresh full live acceptance and final implementation review are
pending ENG-87. Existing historical evidence remains useful but cannot establish
current compatibility by itself.
