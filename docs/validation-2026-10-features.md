# October feature implementation validation

Date: 2026-10-02
Status: Automated source checks pass. Final execution review, artifact gate,
native acceptance, CI and final delivery identity are pending.

This report tracks ENG-99 through ENG-105 from the
[feature frontier research](research/comparable-tool-feature-frontier-2026-10-02.md),
plus the necessary ENG-106 transport repair found during implementation review.
It continues the [backlog validation](validation-2026-10-backlog.md), whose
runtime and investigation results belong to earlier revisions.

ENG-88 remains in Backlog at the maintainer's explicit request. Development may
continue, while master promotion, release tagging and Registry publication remain
separate approval gates. Master and release tags are unchanged by this work.
Freeform remains the only bundled task profile; the earlier negative quality
decisions are unchanged and no additional automatic evaluation round is opened.

## Candidate identity and environment

Baseline and rollback: `3945082cb9cfa6b034fa4ffb112fcfe69cf3876d`.
The implementation is being committed in bounded slices on `dev`:

| Slice | Recorded commit |
| --- | --- |
| Response deadlines and request-local cancellation | `f3e741c056a11953ecc8252df0505de4c858a1a2` |
| Local router presets and explicit inheritance | `a67c19144f861a45d9b237ace372d51af71eac32` |
| Profile library authoring with revision checks | `fdab5f0efe5b0a0771909877752dc01a6fdfe42b` |
| Combined executable and final documentation | **Pending final commit identity** |

The full automated run below used the combined implementation worktree, including
the remaining uncommitted slices. The three recorded commits alone do not identify
that complete tested tree. Final acceptance must record the executable commit,
artifact hashes and any later documentation-only commits separately.

Source checks ran on Linux/WSL with Python 3.14.2 and Node.js 24.13.0. The current
transport environment used Requests 2.34.2 and urllib3 2.7.0. Runtime dependency
declarations and the lock file are unchanged from the baseline.

Native verification targets the maintained Windows Comfy installation and the
already installed model/runtime inventory. Its actual Python, Comfy, frontend,
llama.cpp, driver, model and projector identities must be recorded during that
run. The existing audio compatibility trial and earlier Windows reports do not
establish acceptance for this integrated public surface.

## Implemented scope and automated boundaries

| Issue | Surface | Evidence covered by the source suite |
| --- | --- | --- |
| ENG-99 | [Result JSON and graph composition](graph-composition.md) | Typed result serialization; native JSON field extraction and literal concatenation wiring; separate frozen-prompt render with no llama.cpp nodes; current package/core schemas, typed links and widget ordering. |
| ENG-100 | [Local router presets](router-presets.md) | Bounded local INI parsing, aliases, shared/named precedence, explicit override/inherit policy, source hashes, owned snapshots, same-path edits, reuse, failed replacement preservation and exact snapshot cleanup. |
| ENG-101 | [Explicit text messages](messages.md) | Immutable versioned history, exact role/text preservation, limits and duplicate-key rejection, conflicting system sources, current-turn-only images, absent-history compatibility and history provenance. |
| ENG-102 | [Complete-request budgeting](request-budget.md) | Counting the final prepared payload, exact router resolution, effective per-slot context, unknown versus zero, report/enforce behavior, shared deadline, cancellation, release and rejection of stale-budget authority. |
| ENG-103 | [Independent image captions](captions.md) | Ordered image/prompt/seed/ID alignment, 1 to 32 items, per-item outcomes, one group timeout/release boundary, middle-item failure, cancellation, BaseException cleanup, exact router targeting and aggregate bounds. |
| ENG-104 | [Profile authoring](task-profile-authoring.md) | Current-user isolation, bounded import/export, revision conflicts, atomic replacement, stale response disposal, explicit snapshot update/undo and no automatic workflow rewrite on library save. |
| ENG-105 | [Experimental audio transcription](audio-transcription.md) | Mono 16 kHz PCM validation, ten-second bound, in-memory WAV, approved pair identity, owned-direct/audio capability checks, 128-token limit, exact verified prefix parsing, raw preservation and canonical failure/release behavior. |

The node registry now contains 25 nodes: the 17 released nodes plus Generate,
Task Profile, Result JSON, Append Message, Messages from JSON, Request Budget,
Caption Batch and experimental Transcribe. Released contracts and historical
workflow fixtures remain covered. New fields are appended; earlier canonical
examples omit messages and keep budgeting Off through compatible defaults.

The five new workflow recipes use 0.4 candidate schemas. Core-node schemas were
read from ComfyUI `8d534945ebd53cff61e8def81757c6a6c1b9cf2d` without importing its
runtime. Those checks verify graph structure, not model output quality, browser
round trips or native execution. Generate's Full Batch still sends multiple
images in one request; Caption Batch creates independent requests.

## Necessary transport repair (ENG-106)

Implementation review reproduced an SSE response-header deadline gap: a nominal
100 ms budget returned after roughly 652 ms under trickled headers, while the
JSON control returned after roughly 101 ms. Existing JSON deadline behavior did
not cover that SSE establishment path.

Chat and model-event SSE now capture the absolute deadline while constructing
the response. The response reader retains that deadline after the request-local
context exits. Tests cover fragmented status lines, headers, bodies, chunked and
compressed responses, HTTPS, HTTP proxies and nested TLS records. The change
retains HTTP error classification and request-local isolation.

ENG-102 counting also requires cancellation during blocked JSON response reads.
The transport monitor targets only the current request's borrowed socket and
detaches before reuse. Minimum-version review found that shutdown without close
could leave that socket pooled after a cancelled short-body EOF. The correction
closes the exact socket under the detach lock and joins the monitor before exit.
A negative probe restoring shutdown-only behavior reproduced the open descriptor;
the corrected behavior closes it and preserves concurrent sibling requests.

Current dependencies passed **170 targeted tests plus 86 subtests**. The declared
Requests floor, 2.31.0, paired with urllib3 1.26.20 passed **132 transport tests
plus 86 subtests**. Six additional independent loopback probes passed on both dependency
combinations, including nested TLS cancellation, sibling-request survival,
BaseException propagation and slow HTTP error-body classification.

The minimum-version environment did not run the additional budget-contract suite:
its offline environment lacked a matching cached NumPy wheel. Those tests pass
in the current environment. No new package was downloaded for this check.
These response-read guarantees do not claim bounded DNS/connect/upload
cancellation. Closing a count connection does not prove upstream tokenization
stopped; owned terminal release supplies separate cessation evidence.

## Automated checks and independent review

| Check | Recorded outcome |
| --- | --- |
| Full source Python suite | **1,647 passed plus 197 subtests**, 20.98 seconds |
| Full frontend suite | **98 passed**, zero failures or skips |
| Ruff check and formatting | Pass; **108 files already formatted** |
| Example, graph, routes, messages and caption integration | **192 passed** |
| Final bridge/profile-route inventory correction | **25 passed** |
| Documentation relative links and diff whitespace | Pass |

The counts above are separate verification runs and must not be added together.
The full source result includes the corrected profile-library route inventory.
The tests use isolated fixtures and import-time Comfy stubs where appropriate;
they do not initialize an actual Comfy model runtime.

Independent source/fixture reviews have completed for router presets, explicit
messages and profile authoring. Router review corrected an equivalent reserved
projector flag alias and qualified projector precedence against b9957. Profile
review corrected an import preview that incorrectly blocked a valid replacement
when a different merge strategy exceeded the document limit. Follow-up checks
passed without weakening the bounds.

Transport cross-review passed for count cancellation and the combined behavior
described above. That reviewer authored the earlier SSE guard; its result does
not substitute for the separate combined execution review. **Final independent
review of admission, budgeting, caption groups, transcription and their shared
execution/release paths remains pending.** Native browser rendering, focus and
interaction are also pending; fake-DOM tests do not establish those outcomes.

Commands used for the source and targeted checks:

```bash
.venv/bin/python -m pytest -q
node --test tests/js/*.test.mjs
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python -m pytest -q tests/test_example_workflows.py tests/test_graph_composition.py tests/test_comfy_routes.py tests/test_messages.py tests/test_captions.py --tb=short
.venv/bin/python -m pytest -q tests/test_comfy_bridge.py tests/test_comfy_routes.py --tb=short
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_count_transport.py tests/test_http_deadlines.py tests/test_client_contract.py tests/test_streaming.py tests/test_request_budget.py
uv run --offline --isolated --no-project --with 'requests==2.31.0' --with 'urllib3==1.26.20' --with 'pytest>=9.0' --with psutil python -m pytest -q -p no:cacheprovider tests/test_count_transport.py tests/test_http_deadlines.py tests/test_client_contract.py tests/test_streaming.py
git diff --check
```

## Distribution evidence

A provisional offline build produced a wheel and source archive. Manifest checks
found **80 runtime assets, 22 workflow assets, 65 source-test files and two
benchmark protocols**, with no duplicate members. The isolated wheel imported
all 25 registrations and all packaged Python modules from the extracted wheel.
Runtime/frontend assets were present. Deliberately removing `runtime/client.py`
or `web/generate.js` was rejected, and the missing runtime module could not be
imported through a checkout fallback.

That artifact snapshot predates the final route inventory fix and subsequent
documentation edits. It is provisional packaging evidence, not the final release
artifact. The new tests require no additional documentation or fixture extensions
beyond the existing source manifest. Research documents remain excluded from the
runtime wheel.

The no-isolation build attempt failed because setuptools was absent from the
project virtual environment. `uv build --offline` succeeded using cached declared
build tools without changing dependencies. The wheel-only check invoked the
existing manifest, isolated import and damaged-artifact helpers; it did not run
the full extracted-source suites.

**Pending final artifact gate:** rebuild from the final candidate, record wheel
and source SHA-256 values, then run the complete isolated wheel/extracted-source
Python and JavaScript checks. Portable commands for that pending gate are:

```bash
uv build --offline --out-dir dist
.venv/bin/python tests/check_distribution.py dist
.venv/bin/python tests/smoke_distribution.py dist --prove-rejection
```

## Pending native acceptance

Record actual host, executable and model/projector identities before these
checks. Retain sanitized prompts, responses, timing, failure evidence and
screenshots with the corresponding candidate identity.

| Surface | Required native evidence still pending |
| --- | --- |
| Graph composition | Import/run the structured field and protected-literal recipes; render twice from the separate saved prompt; confirm no LLM request on render requeue. |
| Router presets | Windows paths, actual inherited versus override settings, same-path edit/reload, invalid replacement preservation, exact catalog IDs, generation, scoped unload and final stop/snapshot removal. |
| Explicit messages | Few-shot draft/refinement role order, counts/hashes, no implicit accumulation on requeue, current-image-only behavior and saved-workflow round trip. |
| Request Budget | Real text and supported multimodal count endpoints, effective context provenance, fit/overflow/unknown policies, exact payload correspondence, Stop/deadline behavior and owned release. |
| Caption Batch | Distinct images with shared and exact-length inputs, ordered IDs/results, middle-item failure, active cancellation, one group release and subsequent allocation. |
| Profile editor | Current-user save/conflict/import/export and explicit snapshot undo; classic graph, Nodes 2.0 and App Mode where the host exposes the action; keyboard/focus and transient-state behavior. |
| Audio transcription | Actual Load Audio wiring with the approved pair, transcript/raw/language/result outputs, downstream text use, missing-capability rejection, active Stop, deadline and terminal release. |
| Shared live surface | Correct execution identity and Stop target, transient fields excluded from saved workflows, old workflow compatibility and release evidence across relevant graph/App Mode views. |

No native success, model quality gain or GPU-driver convergence is claimed for
this new feature set at this draft stage. The earlier short synthetic English
ASR trial remains evidence for its named pair and request shape only.

## Pending delivery completion

- Final independent combined execution review and any resulting fixes.
- Exact executable/final commit identities and final artifact hashes.
- Full final artifact gate and native Windows/browser evidence above.
- CI URL, tested commit and all required job outcomes.
- Maintained Windows clone synchronization to the verified candidate.
- Confirmation that owned test model, Comfy and browser processes are stopped.
- Final evidence reconciliation before closing ENG-99 through ENG-106.

The baseline remains the rollback reference. Revert only the reviewed feature
commits if necessary; preserve unrelated work and user model/profile data.
ENG-88 and release promotion remain separate from these development checks.
