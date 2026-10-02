# October backlog validation

Date: 2026-10-02
Status: Verified development work and investigation decisions complete.

This continues the [October review](research/comfyui-llamacpp-project-frontier-review-2026-10-02.md)
and [candidate hardening](validation-2026-10.md). The maintainer deferred hands-on
ENG-88 to Backlog and requested continued development. Master promotion, tagging
and publication remain gated by explicit approval.

## Candidate and environment

Baseline and rollback: `a466b6b`. Preview implementation: `e94f3df`. Combined
preview/image executable candidate: `f84e227`. Runtime timeout repair: `612bdad`;
concurrency fixture repair: `5a5f289`. Protocol source packaging: `1baf736`.
Final delivery consists of these verified implementation commits plus evidence
documents; runtime code is unchanged after `612bdad`. Stable master `40ff5d7`
and tag 0.3.0 `365986a` are unchanged.

Actual host: Windows 11, Python 3.13.7, ComfyUI 0.37.0 (`8d534945`), frontend
1.53.6, llama.cpp b9957 (`c4ae9a88f`), Torch 2.9.1+cu130, NumPy 2.2.6,
Pillow 12.1.0 and RTX 5090 driver 610.88. Tests use a disposable Comfy user,
explicit disposable SQLite database and output directory on loopback 8188.
Inference uses the parent-owned managed runtime on 18080. Existing user model
files and unrelated processes are preserved.

## Preview tail notice (ENG-91)

Truncated Live Response and Live Thinking fields now begin with
`[Preview truncated: showing the end only]`. This is a transient display change;
backend snapshots, native output text and serialized workflow inputs are unchanged.

A deterministic attached SSE fixture supplied 90,037 response bytes and a similarly
sized thinking field through the real Windows Comfy Generate node. This verifies
the transport/backend/browser path without relying on a model to generate an
arbitrarily long response. It is fixture evidence, not model-quality evidence.

Classic graph, Nodes 2.0 and App Mode each passed:

- Both visible, read-only fields show the notice at terminal completion.
- Native Comfy history retains the complete response, including start/end markers.
- Neither the notice nor response tail enters saved workflow data.
- The next short execution clears the notice and prior text.
- The backend keeps its existing per-pane/event bounds. With both panes populated,
  this fixture retained 32,768 bytes per pane; frontend validation still rejects
  preview payloads exceeding its 64 KiB bound.

Screenshots: [classic](research/assets/2026-10-02-backlog/preview-tail-classic.png),
[Nodes 2.0](research/assets/2026-10-02-backlog/preview-tail-nodes2.png),
[App Mode](research/assets/2026-10-02-backlog/preview-tail-app.png).
All 79 frontend tests pass. Independent review found no P0/P1/P2 issue.

## Image transfers (ENG-92)

Counting uses valid shape metadata without copying tensor contents. First-frame
encoding slices before CPU transfer; full-batch count plus encoding transfers the
batch once. Conversion-only wrappers retain their fallback. PNG encoding remains
unchanged, and cancellation is checked before transfer and between frames.

Instrumented tests establish transfer order/count and byte-exact PNG behavior.
Independent comparison against the baseline matched output/errors across 420
rank, batch, dtype, channel and stride cases. The focused suite passes 189 tests.

Native CUDA benchmark: eight 512 by 512 RGB float32 frames, seed 0, two warmups
per path and seven measured repeats with alternating baseline/candidate order.
Timings synchronize CUDA around each operation and include allocation/PNG encoding.
Both contiguous and noncontiguous tensors requiring gradients produced exactly
identical PNGs and frame counts under first-frame and full-batch policies.

| Input and policy | Baseline median | Candidate median |
| --- | ---: | ---: |
| Contiguous, first-frame count + encode | 22.784 ms | 20.116 ms |
| Strided, requires-grad, first-frame count + encode | 24.570 ms | 21.783 ms |
| Contiguous, full-batch count + encode | 170.497 ms | 177.695 ms |
| Strided, requires-grad, full-batch count + encode | 195.464 ms | 198.532 ms |

These runs show roughly 12% lower first-frame latency. They do not show a
full-batch speedup. PNG work dominates full-batch time and timings vary with the
host. Logical transfer volume for count + encode drops from 48 to 3 MiB for the
first frame and from 48 to 24 MiB for all frames. These are shape/dtype-derived
transfer estimates, not measured PCIe counters. Shape-only counting is about
0.005-0.009 ms in the contiguous run versus 1.485-1.534 ms with materialization.
Individual CPU transfers and PNG encodes remain synchronous.

## Transport timeout classification (ENG-94)

Windows CI exposed a typed urllib3 read timeout wrapped by Requests in
`ConnectionError` before the monotonic deadline reported expiry. It previously
became a generic transport error. JSON response reads, chat SSE and model-event
SSE now recognize that specific wrapped exception and retain timeout classification.
Non-timeout connection errors, HTTP errors, partial-output policy and cancellation
precedence remain unchanged. No request budget or timer was relaxed.

The focused suite passes 101 tests plus 60 subtests on current Requests/urllib3
and the declared dependency floor (2.31.0/1.26.20). Independent review reproduced
five expected failures against the old implementation, verified real loopback
socket cleanup, and found no remaining P0/P1/P2.

A separate extracted-package run exposed fixture timing drift: a nominal 10 ms
fast-response pacing wait lasted 734 ms while every emitted byte forwarded promptly.
The concurrent request fixture now holds its response until the slow request exits,
then sends the body immediately. The 100/200 ms slow deadlines and one-second
surviving request budget are unchanged. An independent mutation sharing deadlines
made both repaired tests fail, proving they still detect broken request isolation.
Stress verification passed 80 HTTP plus 80 HTTPS-proxy iterations with no residual
threads, including a bounded CPU-load interval. This is a test-only repair.

Real Windows candidate `612bdad` passed direct text, structured JSON, vision,
explicit-projector PID reuse, browser Stop in classic/Nodes 2.0 and native release.
Full release cleared the owned process and runtime state. Earlier candidate
`f84e227` supplied the preview-tail and CUDA evidence above; those paths were
unchanged by the transport fix.

## Investigations

The fixed [profile bakeoff](research/task-profile-bakeoff-2026-10-02.md) completed
24 measured calls plus two excluded warmups. The text candidate scored a mean
paired delta of -1.0 and is deferred. Visible Evidence scored +2.0 and qualified
for the separately scoped ENG-95 held-out check. All four initial OCR responses
were wrong; gains must not be described as accurate OCR. Model/projector hashes,
protocol, raw results, scoring and limitations are recorded in the report.

The [held-out follow-up](research/visible-evidence-heldout-2026-10-02.md) completed
another 24 measured responses across Qwen3-VL 4B and MiniCPM-V 4.5, with three
new fixtures and unchanged profile content. Three independent reviewers scored
responses blind to both arm and model; parent review finished before unblinding.
Mean deltas were -0.04445 and -1.23333 respectively, combined -0.63889. The fixed
quality gates failed. **Defer Visible Evidence; Freeform remains the only bundled
profile.** All constraints and latency gates passed, but they do not outweigh
factual task regressions. No additional benchmark round is scheduled.

The [audio investigation](research/llamacpp-audio-compatibility-2026-10-02.md)
records both the installed vision-only rejection and the approved positive
Qwen3-ASR trial. Chat/multipart/SSE return the exact spoken sentence plus the raw
ASR prefix. Prior-active cancellation, reuse and owned release pass. Observed
GPU allocation rose about 2.37 GiB and returned within 2 MiB of baseline. No public
audio contract or bundled profile is introduced by these investigations.

## Verification commands

```bash
.venv/bin/python -m pytest -q
node --test tests/js/*.test.mjs
.venv/bin/ruff check .
.venv/bin/ruff format --check .
uv sync --locked --extra dev --dry-run
uv build --out-dir /tmp/comfyui-llamacpp-backlog-release-check
.venv/bin/python tests/check_distribution.py /tmp/comfyui-llamacpp-backlog-release-check
.venv/bin/python tests/smoke_distribution.py /tmp/comfyui-llamacpp-backlog-release-check --prove-rejection
uvx twine check /tmp/comfyui-llamacpp-backlog-release-check/*
COMFY_NO_TELEMETRY=1 uvx --from comfy-cli==1.12.0 comfy node validate
uvx pip-audit --requirement requirements.txt --progress-spinner off --strict
.venv/bin/python -m pytest -q tests/test_images.py tests/test_generation.py tests/test_node_helpers.py tests/test_canonical_generation.py
.venv/bin/ruff check generation/images.py tests/test_images.py tests/benchmark_images.py
.venv/bin/ruff format --check generation/images.py tests/test_images.py tests/benchmark_images.py
node --check web/generate_live_state.js
git diff --check
```

Native Windows commands use the installed Comfy Python; no dependency installation:

```text
C:\ComfyUI\venv\Scripts\python.exe tests/benchmark_images.py --device cuda --batch 8 --height 512 --width 512 --repeats 7 --warmup 2 --output <evidence>/images-cuda.json
C:\ComfyUI\venv\Scripts\python.exe tests/benchmark_images.py --device cuda --batch 8 --height 512 --width 512 --repeats 7 --warmup 2 --noncontiguous --requires-grad --output <evidence>/images-cuda-strided.json
```

Full source suite: **1,141 passed plus 166 subtests**. Native Windows: **1,097
passed plus 166 subtests**, with 44 POSIX/Linux-specific skips. Frontend: **79
passed**. Ruff, formatting, lock dry-run, JavaScript syntax, Twine, Registry
validation and dependency audit pass. Registry validation retains its existing
nonfatal warning about an inert test harness using `exec`. The dependency audit
reported no known vulnerabilities.

The isolated wheel imports all 19 node registrations and runtime/frontend assets.
The extracted source archive passes the same 1,141 Python tests plus 166 subtests
and 79 JavaScript tests without an editable-checkout fallback. Deliberately missing
runtime/frontend files are rejected. The new held-out harness exposed a missing
protocol document in the source archive; `1baf736` includes only the two required
protocol docs, and the checker rejects missing protocols or research leaked into
the wheel. No frozen experiment source or output changed during this repair.

CI run **37019559605** at `1baf736` passes all seven jobs, including Windows,
quality and Python 3.10 through 3.14. The previous protocol package failure at
`2abd70e` is superseded by this passing run. Earlier runtime/fixture candidate
`5a5f289` also passed all seven jobs (37017688073).

Native Windows uses the existing Comfy environment and the already-isolated
pytest dependency directory created for the original October review:

```bash
/mnt/c/ComfyUI/venv/Scripts/python.exe -c 'import sys; sys.path.insert(0, r"C:\Users\vi7or\AppData\Local\Temp\comfyui-llamacpp-review-20261002\pytest"); import pytest; raise SystemExit(pytest.main(["-q"]))'
```

Run that command from the maintained Windows clone. Detailed JSON, request/history
records, fixture generators, browser scripts and CI receipts are retained in
Project KB `evidence/2026-10-02/backlog/`. Owned cleanup is verified: both
parent-owned model runtimes released through
native `/free`, the audio probe released its Windows Job, the browser session
stopped, and the isolated Comfy launcher/child processes exited. Ports 8188,
18080, 18081 and 18890 are closed. The queue was empty and runtime idle before
Comfy shutdown. The final evidence-only commit is synchronized to `dev` and the
maintained Windows clone; the exact receipt is retained in the Project KB.

## Rollback

Revert the focused commits on dev, or restore the pre-phase candidate `a466b6b`
in the maintained installation after stopping its owned runtime. Master and
released artifacts are unchanged. Test-generated files live in isolated evidence,
input-subfolder and temporary user/output locations.
