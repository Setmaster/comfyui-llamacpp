# Dev review and current Windows ComfyUI validation

Date: 2026-10-21. Runtime source: `6f6186e928229fb8dac016fa38ec579ee106e25a`.
Later test and documentation updates do not change runtime code.

This review starts at `fca0a4517cf6fd347d7ff5b6220dcfcc1e71e047` and
revalidates the installed Windows runtime rather than treating the
[October 2 evidence](validation-2026-10-features.md) as current acceptance.
The reviewed source is intended for `master`; `dev` remains the development
branch. Registry publication and release tagging are separate operations.
The immutable `0.3.0` tag remains
`365986af4a47426b5513b3cee917ebec93a4204a`.

## Repairs

- `71223ee`: stalled SSE header, body and HTTP-framing reads now observe
  cancellation promptly. A separate monitor lifetime keeps cancellation active
  while request context resets before model-event generator yields. Cancelled HTTP error-body
  reads remain cancelled, genuine error-body deadlines retain HTTP status,
  partial text remains marked, and exact stream DELETE/response cleanup runs on
  every exit. New real-socket tests cover cancellation, callback interruption,
  generator close, context isolation, later requests and monitor cleanup.
- `6f6186e`: image socket insertion captures each input's link before reordering
  it. Current frontends derive `input.link` from the numeric slot; reading it
  after splicing could move a Start Server URL link onto an image and eventually
  discard it. Updates now use the authoritative link-store API when available,
  with the earlier frontend fallback retained. The getter-backed regression
  fails against the earlier source and passes with the fix.
- Documentation now distinguishes the 25-node 0.4 source version from published
  Registry 0.3, enumerates all eight additions and rollback limits, and extends
  the acceptance checklist to the newer feature surfaces.

Independent backend and frontend reviews, followed by independent review of each
repair, returned no unresolved findings. These are bounded correctness reviews,
not a claim that every possible environment or model has been tested.

## Environment and automated gates

Native Windows installation: ComfyUI 0.41.0 at
`fc04da91`, frontend 1.55.17, Python 3.13.7, Torch 2.9.1+cu130,
llama.cpp b9957 (`c4ae9a88f`), NVIDIA RTX 5090. Tests enable only this custom-node
pack and use separate user, database, input, output and temporary directories.
The maintained clone is `C:/ComfyUI/custom_nodes/comfyui-llamacpp`.

| Check | Outcome |
| --- | --- |
| WSL Python 3.14.2 full suite | 1,681 passed, 197 subtests passed |
| Native Windows Python full suite | 1,637 passed, 44 platform skips, 197 subtests passed |
| Frontend Node.js suite | 111 passed, no failures/skips |
| Ruff lint and formatting | Passed, 109 Python files formatted |
| JavaScript syntax and diff whitespace | Passed |
| Build, Twine and distribution manifests | Passed; 80 runtime assets, 22 workflow assets, 66 source-test files, two benchmark protocols |
| Isolated wheel | All 25 registrations and runtime/frontend assets imported successfully |
| Damaged distributions | Missing `runtime/client.py` and `web/generate.js` rejected |
| Extracted sdist | 1,681 Python tests, 197 subtests and 111 JavaScript tests passed |
| Pinned Registry validation | Passed with its existing nonfatal warning about the AST test helper's `exec` |
| Runtime requirements audit | No known vulnerabilities in the resolved requirements |

Separate verification runs are not additive. The platform skips concern POSIX
and Linux process mechanics. Unit tests use their normal isolated Comfy stubs;
actual runtime evidence is recorded below. Registry validation does not publish.

The first delivery CI exposed a race in the new cancellation fixture's reader
recorder: attachment can cancel, close and raise before construction returns.
The recorder now retains the partial reader before initialization, preserving
the cleanup assertion. Four deterministic regressions fail before that fixture
repair and pass afterward; ten repeated 26-test cancellation runs pass, as do
52 transport tests plus 42 subtests on both Python 3.11 and 3.13.
One extracted-package run also hit the previously observed proxy first-use
timing failure. Its deadline assertions are unchanged; 12 focused repeat runs
pass, and final complete package/CI results are recorded separately.

The applicable commands remain those in the README and project guide, including
`uv sync --locked --extra dev`, full pytest/JavaScript suites, Ruff, `uv build`,
Twine, both distribution scripts, `comfy-cli==1.12.0` node validation and
`pip-audit --requirement requirements.txt --progress-spinner off --strict`.
Final artifact identities, CI and branch readbacks are recorded on ENG-88 to
avoid embedding a self-referential final commit in this report.

## Native model and browser checks

All API stages below pass on the named runtime revision. Text and vision use the
installed Qwen3-VL-4B-Instruct Q5_K_M with its metadata-matched Q8 projector.
Router fixtures expose those installed weights under two explicit local preset
IDs. Audio uses the exact supported Qwen3-ASR-0.6B Q8_0 pair. Nothing is downloaded.

| Scenario | Observed result |
| --- | --- |
| Direct text and reuse | Real `READY` response; later generation/counting reuses the owned PID. |
| Structured output | Constrained JSON parses with the requested field. |
| Explicit history | Supplied user/assistant history produces the exact remembered `BLUE27` code. |
| Budgets | Count-only path completes without generation; enforced fitting requests complete with usage; overflow raises. |
| Vision and captions | Automatic projector works; red and blue fixtures produce correctly ordered, separately identified captions. |
| Strict failures | Invalid URL and budget overflow raise; terminal release on an unowned loopback fixture is rejected before submitting a prompt. |
| Direct release | Result confirms terminal cleanup and status becomes unowned. |
| Router presets/isolation | Both exact IDs load. Counting/generation/scoped release unload A, preserve loaded B and preserve router PID; B remains callable. Explicit Stop removes the router. |
| Supported audio | Native Load Audio wiring transcribes the generated mono 16 kHz sentence, `The blue cup is on the table.` Terminal release removes the owned runtime. |
| GPU handoff | After LLM release, a core-only SDXL graph renders a 512 by 512 PNG without restarting ComfyUI or starting llama-server. Runtime epoch is unchanged. |
| Legacy nodes | Basic, ADV and ADV++ each return `LEGACY` through native Prompt Output. |
| Templates | All 16 bundled workflows load and convert to API graphs without missing node types. This is structural coverage; execution is limited to the scenarios above. |
| Classic and Nodes 2.0 | Explicitly tested both modes: image count 0/1/10/1/0 preserves the exact Start URL link and correct socket count, including save/reload. |
| App Mode | Real live response/status updates, followed by an actual click on a pinned `Stop generation` action, produce a cancelled partial state and zero active generation leases. The test explicitly adds the Stop pin to the example layout. Strict cancellation withholds terminal sockets and Comfy displays the resulting node failure. |
| Profile editor | Native editor creates/saves a user profile without changing the saved workflow snapshot; explicit Update Saved Snapshot copies the selected profile. |

Browser checks use the visible Windows browser. Page error and unhandled-rejection
listeners report no uncaught errors during the exercised graph, editor and Stop
flows. This is not a complete historical console log. Screenshots and exact
JSON histories are retained with the private review evidence. Earlier frontend
App Mode central-text limitations remain historical observations; native history
and explicit live fields provide the response evidence here.

The audio sentence is an integration fixture, not an accuracy benchmark. GPU
handoff establishes actual execution and terminal process state; no quantitative
driver-wide memory-return claim is made. Earlier fixture mistakes (caption JSON
uses `rows`, an exact owned URL is still managed, and global router release may
retain an unloaded router) were corrected without changing product behavior.

## Cleanup and rollback

The final queue is empty, owned LLM release is terminal, Comfy-managed models are
freed, and the exact isolated Comfy launcher/child PIDs 106708 and 104404 have
exited. Ports 8188 and 18080 are closed. Only the task's browser tab and
agent-owned browser session are closed; other browser tabs are preserved.

Revert the two repair commits for a focused code rollback, or return the source
clone to the review baseline. The former stable branch points to
`40ff5d730dc27cb51d68ae53142cab9b4c91f5af` before promotion. The immutable 0.3.0
tag remains the published-version rollback. Preserve user workflows, profiles
and installed model files when switching source revisions.
