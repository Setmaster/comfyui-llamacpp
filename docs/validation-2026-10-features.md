# October feature implementation validation

Date: 2026-10-02
Status: Source checks, independent execution review, interim distribution checks,
native feature scenarios, profile browser acceptance and CI on `6fab80a` pass.
Remaining native failure/Stop scenarios, final artifacts and delivery identity
are pending.

This report tracks ENG-99 through ENG-105 from the
[feature frontier research](research/comparable-tool-feature-frontier-2026-10-02.md),
plus the necessary ENG-106 transport repair found during implementation review.
It continues the [backlog validation](validation-2026-10-backlog.md), whose
runtime and investigation results belong to earlier revisions.

ENG-88 remains in Backlog at the maintainer's explicit request. Master promotion,
release tagging and Registry publication remain separate approval gates. Master
and release tags are unchanged by this work. Freeform remains the only bundled
task profile; the earlier negative quality decisions are unchanged and no
additional automatic evaluation round is opened.

## Candidate identity and environment

Baseline and rollback: `3945082cb9cfa6b034fa4ffb112fcfe69cf3876d`.
The implementation is committed in bounded slices on `dev`:

| Slice | Recorded commit |
| --- | --- |
| Response deadlines and request-local cancellation | `f3e741c056a11953ecc8252df0505de4c858a1a2` |
| Local router presets and explicit inheritance | `a67c19144f861a45d9b237ace372d51af71eac32` |
| Profile library authoring with revision checks | `fdab5f0efe5b0a0771909877752dc01a6fdfe42b` |
| Combined messages, budgets, captions and transcription | `b5b747d77faf9118ff4f11edb597a6b778bf6f6f` |
| Opt-in streamed usage for budgets and media operations | `7a353a6495e1ccefa64458eaec0426a5d1a89341` |
| Bounded test parameter IDs for Windows environment limits | `4ebbc976931cc70cc4f7401c90cb3ff2023d4c8f` |
| Windows cancellation/constructor repair | `6fab80acb1483f73977858ad07ac00739e5933e9` |
| Sampling control visibility on Caption Batch and Request Budget | `370a13f` |

The latest full source run includes the transport repair subsequently committed
as `6fab80a`. Interim packages were built from an immutable export of `7a353a6`.
The successful native API stages below used the integrated usage-enabled surface;
`4ebbc97` changes test IDs, not runtime behavior. Final acceptance must record the
repaired executable commit, artifact hashes and later documentation-only commits
separately.

Source checks ran on Linux/WSL with Python 3.14.2 and Node.js 24.13.0, using
Requests 2.34.2 and urllib3 2.7.0. Runtime dependency declarations and the lock
file are unchanged from the baseline.

Native checks use the maintained Windows 11 installation: Python 3.13.7,
ComfyUI 0.37.0 (`8d534945`), frontend 1.53.6, llama.cpp b9957 (`c4ae9a88f`),
Torch 2.9.1+cu130 and RTX 5090 driver 610.88. The disposable Comfy user, database,
input and output roots are separate from user data. Only this custom-node package
is enabled for these checks. A current `/system_stats` receipt confirms the
Python, Comfy, frontend, Torch and GPU inventory. Final environment and executable
identity reconciliation remains pending after the transport repair.

Text and vision scenarios use the installed Qwen3-VL-4B-Instruct Q5_K_M model
with its existing Q8 projector. Audio uses the explicitly approved Qwen3-ASR-0.6B
Q8_0 pair. Frozen renders use the installed SDXL `bigLove_photo5.safetensors`
checkpoint. These are bounded integration fixtures, not model-quality benchmarks.

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

The registry contains 25 nodes: the 17 released nodes plus Generate, Task Profile,
Result JSON, Append Message, Messages from JSON, Request Budget, Caption Batch and
experimental Transcribe. Released contracts and historical workflow fixtures
remain covered. New fields are appended; earlier canonical examples omit messages
and keep budgeting Off through compatible defaults.

There are 16 workflow JSON files and six JPEG thumbnails, totaling 22 packaged
workflow assets. Five workflow recipes are new in this feature slice. Their
structural checks use current package schemas and core schemas read from ComfyUI
`8d534945ebd53cff61e8def81757c6a6c1b9cf2d`. Native execution evidence is recorded
separately below. Generate's Full Batch still sends multiple images in one
request; Caption Batch creates independent requests.

## Necessary transport repair (ENG-106)

Review first reproduced an SSE response-header deadline gap: a nominal 100 ms
budget returned after roughly 652 ms under trickled headers, while the JSON
control returned after roughly 101 ms. Chat and model-event SSE now capture the
absolute deadline during response construction, and the reader retains that
deadline after the request-local context exits. Tests cover fragmented status
lines, headers, bodies, chunked and compressed responses, HTTPS, HTTP proxies and
nested TLS records while retaining HTTP error classification and isolation.

ENG-102 counting also needs cancellation during blocked JSON response reads.
The monitor targets the current request's borrowed socket and detaches before
reuse. Minimum-version review found that shutdown without close could leave a
cancelled short-body socket pooled. Closing the exact socket under the detach
lock and joining the monitor fixed that case. A negative probe restoring
shutdown-only behavior reproduced the open descriptor.

Later CI and native Windows checks exposed two further defects: cancellation
could interrupt response construction before `fp` existed, and a Windows timed
socket read could remain blocked after another thread closed the socket. The
`6fab80a` repair initializes partial response state and polls cancellable socket
reads while preserving the original inactivity and absolute deadlines. It does
not reuse a `SocketIO` reader after a timeout. These changes need the final
artifact rerun even though the earlier `7a353a6` package run passed once.

The latest independent review passes **177 targeted tests plus 86 subtests** on
current dependencies. Requests 2.31.0 with urllib3 1.26.20 passes **139 transport
tests plus 86 subtests**; native Windows passes **139 plus 86 subtests**. Ten
supplemental transport cases pass in all three environments. Native transport
tests use Requests 2.32.5 and urllib3 2.6.2 with disposable loopback peers.

An additional successful TLS-in-TLS continuation probe passes in all three
environments. Fragment gaps exceed the 50 ms polling slice: six header polls and
12 body polls time out, then the complete response and later requests succeed on
the same connection with no extra threads. One initial concurrent review run
exceeded an existing proxy timing assertion; the isolated and full sequential
reruns passed unchanged. No timeout threshold was relaxed.

The earlier [CI run 37045392726](https://github.com/Setmaster/comfyui-llamacpp/actions/runs/37045392726)
exposed the constructor/capture races. Native Windows also exposed oversized
pytest parameter IDs exceeding its environment-variable limit; `4ebbc97` gives
those cases bounded IDs without changing payloads or assertions. The subsequent
[CI run 37048580031](https://github.com/Setmaster/comfyui-llamacpp/actions/runs/37048580031)
passes all seven jobs on `6fab80a`. Later delivery commits still require their own
CI readback.

The isolated minimum-version environment did not run the additional budget
contract suite because a matching NumPy wheel was not cached. Those tests pass
in the current environment. No dependency was added to work around that limit.
These response-read checks do not establish bounded DNS, connect or upload
cancellation. Closing a count connection also does not prove upstream
tokenization stopped; owned terminal release supplies separate cessation evidence.

## Automated checks and independent review

| Check | Recorded outcome |
| --- | --- |
| Full source Python suite, including the `6fab80a` transport repair | **1,655 passed plus 197 subtests**, 22.52 seconds |
| Full maintained Windows clone suite on `6fab80a` | **1,611 passed, 44 platform skips, 197 subtests**, 31.29 seconds |
| Full frontend suite after sampling visibility correction | **105 passed**, zero failures or skips |
| Ruff check and formatting | Pass; **108 files already formatted** |
| Example, graph, routes, messages and caption integration | **192 passed** |
| Bridge/profile-route inventory correction | **25 passed** |
| Combined execution review and supplemental probes | **145 passed**, no unresolved P0/P1/P2 findings |
| Focused usage-option review | **132 passed plus 34 subtests**, no P0/P1/P2 findings |
| GitHub Actions on `6fab80a` | **All seven jobs passed**, run 37048580031 |

These are separate verification runs; their counts must not be added together.
The source suites use isolated fixtures and import-time Comfy stubs where needed,
not an actual model runtime.

The Windows run used its installed Python and the retained `run-tests.py`
wrapper against the maintained clone. All 44 skips are POSIX/Linux process-group,
signal, identity or pidfd assertions. The clone was then restarted successfully
on `6fab80a`; that healthy restart is distinct from the remaining native feature
scenarios on the repaired executable.

Independent reviews completed for router presets, explicit messages and profile
authoring. Router review corrected a reserved projector flag alias and qualified
projector precedence against b9957. Profile review corrected an import preview
that blocked a valid replacement when a different merge strategy exceeded the
document limit.

Combined execution review found and verified a caption release-boundary repair:
an external global release now stops later item submissions while the group
holds its lease. Strict mode fails; marked partial mode preserves completed rows
and leaves remaining positions blank. Admission, deadlines, exact cancellation
targets, typed results, audio preparation and terminal release were reviewed.

The first native budget trial counted 67 text tokens but received no usage event.
`7a353a6` requests `stream_options.include_usage` for budget operations, captions
and transcription. Generate with budgeting Off keeps its previous payload shape.
Independent review confirmed that count and generation use the same final
payload; the restarted native text and vision checks now match actual usage.
Usage remains nullable when an upstream server does not supply it.

Native browser review found that Caption Batch and Request Budget exposed expert
sampler widgets while Default mode ignored their values. `370a13f` separates
sampling support from Generate-only discovery and reuses the existing visibility
hooks. Default/custom toggles, configure/reload, linked inputs, exact saved values
and Transcribe isolation pass seven new frontend tests. Independent source review
passes; the final native browser recheck is recorded separately below.

Commands used for source and focused checks include:

```bash
.venv/bin/python -m pytest -q
node --test tests/js/*.test.mjs
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python -m pytest -q tests/test_example_workflows.py tests/test_graph_composition.py tests/test_comfy_routes.py tests/test_messages.py tests/test_captions.py --tb=short
.venv/bin/python -m pytest -q tests/test_comfy_bridge.py tests/test_comfy_routes.py --tb=short
.venv/bin/python -m pytest -q tests/test_budget_execution.py tests/test_caption_execution.py tests/test_transcription_execution.py tests/test_streaming.py
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_count_transport.py tests/test_http_deadlines.py tests/test_client_contract.py tests/test_streaming.py tests/test_request_budget.py
uv run --offline --isolated --no-project --with 'requests==2.31.0' --with 'urllib3==1.26.20' --with 'pytest>=9.0' --with psutil python -m pytest -q -p no:cacheprovider tests/test_count_transport.py tests/test_http_deadlines.py tests/test_client_contract.py tests/test_streaming.py
git diff --check
```

Supplemental reviewer probes and native harness receipts are retained in the
private evidence bundle. Their counts above are not claims that the listed
repository-only commands reproduce those extra cases.

## Distribution evidence

An immutable export of `7a353a6` passed the complete interim package gate using
cached build and validation tools. Manifests contain **80 runtime assets,
22 workflow assets, 65 source-test files and two benchmark protocols**, with no
duplicate members. The isolated wheel imports all 25 registrations and all its
Python modules from the extracted wheel. Runtime/frontend assets are present.
Removing `runtime/client.py` or `web/generate.js` is rejected, and a missing
runtime module cannot import through a checkout fallback.

The extracted source archive passed **1,648 Python tests plus 197 subtests** and
**98 JavaScript tests**. Research documents remain excluded from the runtime
wheel. Twine passed both archives. Registry validation exited successfully but
warned about `exec` in the AST-based test helper
`tests/test_comfy_model_validation.py`; the validator says that rule may become
an error. No Registry upload or publication was performed.

The requirements audit reported no known vulnerabilities. Its scope is the
resolved `requirements.txt` dependencies, not every platform-specific lock-file
entry or the native llama-server executable.

| Interim artifact from `7a353a6` | Bytes | SHA-256 |
| --- | ---: | --- |
| `comfyui_llamacpp-0.4.0-py3-none-any.whl` | 656,252 | `b677861c13dc05616a87cb10280c49c9974e59e8fef72943eca0a154e8118b84` |
| `comfyui_llamacpp-0.4.0.tar.gz` | 844,035 | `66dcfe4d090bafce76873d32fd98e47b0f2bbfd2e49f1f4b30e588c7049e8c37` |

These are interim hashes. Final artifacts must be rebuilt after the transport
repair. The gate used uv 0.9.28, Twine 7.0.0, comfy-cli 1.12.0 and pip-audit
2.10.1. The commands below show the same checks with `dist` as the output
placeholder; the recorded run used its own disposable artifact directory:

```bash
uv sync --locked --extra dev --dry-run
uv build --offline --out-dir dist
.venv/bin/python tests/check_distribution.py dist
.venv/bin/python tests/smoke_distribution.py dist --prove-rejection
uvx --offline twine check dist/*
COMFY_NO_TELEMETRY=1 uvx --offline --from comfy-cli==1.12.0 comfy node validate
uvx --offline pip-audit --requirement requirements.txt --progress-spinner off --strict
```

## Native API evidence established

The saved stage summaries are `passed` for composition, messages-budget,
captions, presets, audio and frozen-render. The messages-budget stage includes
two deliberate overflow errors; these are expected rejection proofs, not
successful generation runs. A fresh messages-budget run on `6fab80a` again passes
the exact 67/101 token comparisons, 4,096-token slot context and terminal release.
The other stage results below retain their earlier usage-enabled source identity.

| Surface | Established native result |
| --- | --- |
| Graph composition | Structured JSON's `positive_prompt` field passes through native `JsonExtractString` exactly. Native concatenation retains the protected literal `Mira Vale, (blue coat:1.2), example_tag` exactly. Both canonical results complete and terminally release the owned runtime. |
| Explicit messages | Append Message and Messages from JSON preserve a two-message user/assistant history, including whitespace and order. Generation returns the requested `BLUE_NOTEBOOK_7` code, with message count two in result provenance. |
| Request Budget | Text counts **67** input tokens and vision counts **101**, each matching streamed `usage.prompt_tokens`. Standalone observations and Generate report mode have identical payload hashes and budget observations. Both use the actual **4,096-token slot context**, rather than the configured 8,192-token total. Requests exceeding the slot through `max_tokens=4097` fail with `invalid_request`; final owned release completes. |
| Caption Batch | Two distinct images produce `Red` and `Blue` in order. List output equals aligned JSON rows; IDs `red-first`/`blue-second` and seeds 101/202 remain exact. Each result reports one image. The group completes and terminally releases its owned direct runtime. |
| Router presets | Absolute Windows model/projector paths work with exact catalog ID `eng100-vision`. Inherit uses context **2,048**. Catalog reload retains the staged snapshot and process, so an edited source has no effect until Start is requeued; requeue changes the runtime epoch and applies **3,072**. Override applies the node's **4,096**. Scoped release unloads the selected model while retaining the router PID. |
| Audio transcription | Actual `LoadAudio` feeds the approved pair through Transcribe. The **44,720-sample, 2.795-second**, mono 16 kHz fixture returns `The blue notebook is beside the window.` The exact `language English<asr_text>` prefix, raw response, English language output, typed operation, model/projector hashes and prepared-WAV hash are preserved. Usage reports 49 prompt and 12 completion tokens. Metadata excludes waveform data; terminal direct release leaves no owned runtime PID. |
| Frozen render | Two separate **512 by 512** core-only graphs render from saved prompt text, with changed text/seed on the second run. Each PNG retains the exact submitted text. Neither graph contains llama.cpp nodes; the runtime remains unowned with no PID or active generation, and its epoch and log remain unchanged. |

The original audio fixture SHA-256 is
`4d141e9f9d520d95620d98f12719c77e36d49ddca4653e50b7c52c75c344598b`.
The prepared WAV has SHA-256
`ec3e2ed190c8dee32f96f8bb8b7cd85e43e054f53c3226a11e78d4035105f0da`;
the distinction reflects the public AUDIO-to-PCM conversion. This short synthetic
English example establishes its specific integration path, not general ASR
accuracy or broader audio/model support.

Additional native stages on `6fab80a` establish these paths:

| Scenario | Result |
| --- | --- |
| Native image list mapping | Core `RebatchImages` maps ordinary Generate over red and blue images, producing two complete one-image results in order. A downstream Release Runtime also maps twice, returning complete then no-op. This demonstrates composition, while confirming that a downstream node alone is not a group finalizer after failure. |
| Router isolation and budgeting | Two distinct router IDs backed by the same installed weights are independently loaded. Selected-model counts **44 text / 81 vision** equal generation usage with matching payload hashes and effective slot context **4,096**. Counting, generation and scoped release preserve the other loaded ID and the router PID. This fixture uses the existing F16 projector; direct-count fixtures use the Q8 projector. |
| Invalid preset replacement | An unsupported preset option returns the released StartRouter rejection contract (message plus false), preserving owned PID, epoch and both loaded catalog identities. Subsequent counting/generation succeeds. The first harness incorrectly expected an exception; its failed receipt is retained, and the corrected fresh run verifies the actual return contract. Final explicit stop leaves no owned runtime. |
| Per-item caption prompts | Three exact prompts, including whitespace, produce their distinct requested markers in the original ID/seed order. One group execution identity is observed, and each complete item requests reuse. |
| Real middle-item failure | A stop sequence causes the second model response to be empty. Marked partial output retains `[complete, failed, not_attempted]`, with blank failed/unattempted positions, a protocol error on the second row and terminal group release. |
| Second-item cancellation | After first-item progress and a new second-item response, an exact-generation cancellation returns HTTP 202. The group is cancelled, row alignment remains intact and terminal release completes. No global Comfy interrupt is sent. |
| Native invalid AUDIO | Core EmptyAudio fixtures reject wrong sample rate, stereo, empty, silent and over-ten-second inputs with the expected errors and unchanged owned ASR PID/epoch. Explicit cleanup then completes. |
| Wrong model pair | The existing vision pair is rejected as unsupported for Transcribe, and requested terminal release completes. This is approved-pair identity rejection, not an empirical test of a later missing-audio-capability response. |
| ASR reuse and downstream generation | Two transcriptions use the same ASR process. A real graph then releases ASR, orders the text-model start through a core STRING dependency, passes the actual transcript into Generate and terminally releases the text runtime. |

Caption live receipts establish one observed final release operation ID; exact
internal finalizer call count and global release between items are covered by
isolated RuntimeService tests. Native AUDIO rejection receipts establish the
categorical errors and unchanged runtime; exact zero upstream HTTP calls are
isolated-test evidence. Unknown count/context, missing audio capability, prefix
variants and slow-peer deadlines use executor and loopback transport fixtures.
They are not presented as naturally occurring failures of the installed model.

## Native profile browser acceptance

Independent browser acceptance passes in classic graph, Nodes 2.0 and App Mode,
with no P0/P1/P2 finding. It began on `b5b747d`, continued after the `7a353a6`
restart and captured final checkout identity `4ebbc97`. The profile implementation
files are unchanged across those commits. The preserved library hash and graph
snapshot survived the coordinated restart exactly.

Real editor controls verified exact Unicode, whitespace and multiline saves;
Save Library retained the existing workflow snapshot. Explicit Update Saved
Snapshot applied the new profile, and keyboard undo/redo restored exact snapshots.
Export included unsaved draft text and excluded built-in Freeform. Import merge
and replacement behaved as selected; duplicate IDs and duplicate JSON keys were
rejected without draft mutation.

Two real editor revisions produced the expected HTTP 409 for a stale save,
retaining its draft and the winning library. Deleting a library entry left its
portable workflow snapshot intact through serialization/reload. Nodes 2.0 exposed
all three actions and preserved exact text when inspecting/copying a missing
profile. App Mode exposed the actions, selector and status; Freeform remained
read-only and updating the workflow remained explicit.

Controlled response delivery verified that a stale GET cannot overwrite a newer
acknowledged list, and removing a node during a save disposes the editor without
later mutation of that node. These used real backend responses with bounded
browser interception, not a claim of naturally occurring network timing.
Serialization retained the portable snapshot and excluded drafts/status/transient
widgets; App Mode intentionally retained its action bindings.

The browser report retains 66 checksummed artifacts. The dedicated browser
session and its owned process tree were closed. This gate does not establish
screen-reader accessibility, mobile layout or model-output quality. The initial
screenshot obscured by Comfy's Templates modal is excluded from accepted visual
evidence.

A separate browser round-trip gate imports, serializes and reloads all 16
workflow JSON files in both classic graph and Nodes 2.0: **32 round trips pass**.
It submits no prompt and performs no runtime operation. Checks of the additional
node controls are still in progress; workflow import success does not establish
their live Stop behavior.

## Remaining acceptance and delivery gates

- Complete the independent native sampler-visibility recheck and actual browser
  Stop controls for Caption Batch and Transcribe. Their execution/cancellation
  contracts have separate source, fixture and native API evidence above.
- Rebuild and validate final wheel/source artifacts, with new hashes.
- Reconcile final native environment, model/projector identities and all required
  scenarios against the repaired executable.
- Read back CI for the final delivery commit; all seven jobs already pass on
  `6fab80a` and `370a13f` (run 37049655249).
- Confirm the maintained Windows clone matches the verified candidate.
- Confirm owned test model and Comfy processes are stopped; the profile browser
  session is already closed.
- Reconcile final evidence before closing ENG-99 through ENG-106.

The baseline remains the rollback reference. Revert only the reviewed feature
commits if necessary; preserve unrelated work and user model/profile data.
ENG-88 and release promotion remain separate from these development checks.
