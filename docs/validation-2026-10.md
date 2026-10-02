# October candidate validation

Date: 2026-10-02
Status: Validated dev candidate; maintainer acceptance pending

This records the candidate hardening from the
[October review](research/comfyui-llamacpp-project-frontier-review-2026-10-02.md),
tracked by ENG-79 through ENG-87 and ENG-93 in the
[7dev project](https://linear.app/7dev/project/comfyui-llamacpp-b846a629b382).
The July [0.4 validation](validation-0.4.md) remains historical evidence.

## Candidate and environment

- Source rollback: `dev` at `2d1d5ce` before this work.
- Stable: immutable 0.3.0 at `365986a`; `master` remains `40ff5d7`.
- Package-source revision: `ec0e7b55ff550554dcacd022cd7c68df68798458`.
- Candidate executable revision: `3f6fb5738b733c90bbacf8da83f5eff2c60250df`.
- ComfyUI: 0.37.0, commit `8d534945`.
- Frontend: 1.53.6.
- Windows Python: 3.13.7; PyTorch: 2.9.1+cu130.
- llama.cpp: b9957 (`c4ae9a88f`), existing full Windows CUDA installation.
- GPU: RTX 5090, driver 610.88.
- Test host: loopback ComfyUI on 8188 with only this custom node enabled;
  owned llama-server on 18080; disposable user/output data.

The baseline launched successfully with all 19 nodes. The template browser
listed all 11 examples. An offline Generate request reproduced `Starting...`
remaining visible after its workflow failed. The
[baseline screenshot](research/assets/2026-10-02-frontier-review/baseline-offline-status.png)
preserves that symptom.

## Automated and independent checks

The original baseline passed 912 Python tests plus 66 subtests, 59 frontend
tests, Ruff, formatting, build, manifest and Twine checks. Registry validation
and the runtime dependency audit also passed during this work.

Final combined source passes 1,058 Python tests plus 149 subtests and 75 frontend
tests. Native Windows passes 1,014 tests plus 149 subtests, with 44 POSIX-specific
skips. The extra 22 installed-Comfy validation cases run here through inert host
adapters; CI without a Comfy checkout skips those cases. Ruff and formatting pass
across 80 Python files. Isolated wheel import registers all 19 nodes. Manifest
checks cover 69 runtime assets, 17 workflow assets and 42 source-test files.
Extracted-sdist Python and frontend suites pass with the same local counts.
Deliberately removing a runtime module or frontend asset is rejected.
Twine, Registry validation and the runtime dependency audit pass.

The final package gate initially caught a test observation race: the client
returned at its deadline with a closed descriptor, but a write-only test peer
had not observed disconnect within 300 ms. Reproduction with GC disabled showed
all 30 local sockets closed and descriptors stable at 5. The test now checks
actual local reader/raw/socket closure before session teardown and includes a
paused-peer regression; deadline timing bounds were retained. Test-only commit
`536bf89` adds this correction, with no runtime change after `3f6fb57`.
The focused deadline suite now passes 94 tests plus 43 subtests on both tested
dependency pairs. Source-backed members of the tested sdist (133 files) match
the package-source checkout byte-for-byte.

[GitHub Actions run 36968320754](https://github.com/Setmaster/comfyui-llamacpp/actions/runs/36968320754)
passed all seven jobs at `ec0e7b5`: Python 3.10 through 3.14, native Windows 3.13,
and quality including the isolated package tests. Final evidence-only closeout
commit/CI identity is recorded on [ENG-87](https://linear.app/7dev/issue/ENG-87).

Artifact SHA-256:

```text
wheel  4f6d2c4fb100e09203a2f002f3f6785d50096bbbe986cdd9ff24eb3f81f093a6
sdist  ae8974e4e552776ccea6cadf5908e1208688ad1622e8a736ca13bc7135d7a736
```

Independent review passes with no remaining P0/P1/P2 findings:

- Launch/configuration and Gemma: 395 tests plus 106 subtests.
- Generation/discovery: 158 Python and 39 frontend tests after correcting a
  cross-root alias issue found in the first repair.
- Absolute response deadlines: 93 tests plus 43 subtests on both current
  requests/urllib3 (2.34.2/2.7.0) and the requests 2.31.0 floor with urllib3 1.26.20.
  Independent review found and repaired nested-TLS fragmentation and concurrent
  first-use proxy initialization. Their original reproductions now honor the
  0.2/0.1-second budgets. Across 64 HTTP/HTTPS timeouts with garbage collection
  disabled, descriptors stayed at 5 and no threads remained.
- Model validation: 217 tests, including actual installed Comfy validation,
  preserving compatible linked values and unrelated core validation.

## Runtime acceptance

Checks on executable candidate `be6723a` passed on the actual Windows host:

| Surface | Observed result |
| --- | --- |
| Registration and templates | 19 nodes; all 11 workflow files load in both renderers |
| Classic and Nodes 2.0 | Offline requests show terminal failed status with Stop disabled |
| Dynamic controls | Default/custom sampling and exactly 0, 1 and 10 image sockets |
| Save/reload | Generate and Task Profile roundtrip; fixed seed and token limit retained; transient live fields omitted and restored Idle |
| Direct text | Qwen3-VL 4B Q5 returns `DIRECT_OK` |
| Structured output | JSON Schema response parses as `{"status":"JSON_OK"}` |
| Vision | Automatic Q8 projector describes the fixture's red rectangle and blue circle |
| Explicit projector | Same Q8 selection preserves PID and runtime epoch |
| Text-only mode | Text succeeds; image input fails locally with `capability_unsupported` |
| Live Stop | Both renderers stream text, confirm exact cleanup, finish cancelled and disable Stop |
| App Mode | Exposed controls queue and return `APP_MODE_OK`; status/live widget values update |
| Native release | Direct process stops; router models unload while its control process remains, by design |
| Explicit Stop | Removes the owned router process |
| GPU handoff | SDXL 512x512 four-step generation succeeds after LLM release without restarting ComfyUI |

The App Mode central preview remains empty on this frontend, as documented in
the historical validation. The response remains available through the native
history API and Live Response field. This is not a claim that the upstream
central text-preview limitation is fixed.

Driver memory moved from 11,543 MiB before direct start to 16,156 MiB after text,
16,238 MiB after vision, and about 11,483 MiB after direct unload. The subsequent
SDXL run reached 18,279 MiB and returned near 11,653 MiB after native free.
These are observations on a shared desktop GPU, not an isolated-memory benchmark.

This pass exposed ENG-93: selecting a discovered public router ID was rejected by
Comfy's static combo validation. After its correction, candidate `3f6fb57` passed
real `/prompt` generation with `ROUTER_OK`, then browser Refresh discovered 14
router models, selected the exact public ID and returned `ROUTER_UI_OK`.
Terminal live state reported successful model-scoped release; passive discovery
confirmed all models unloaded while the router PID remained available. The
operation released only `qwen-vl-4B-Instruct` and left no active generations.
A final direct text/JSON pass on the same revision succeeded; native `/free`
removed PID 41504, returned the runtime to idle and driver memory from 16,196 MiB
to 11,523 MiB (11,522 MiB before allocation).

Public screenshots: [classic failure](research/assets/2026-10-02-frontier-review/offline-classic.png),
[Nodes 2.0 failure](research/assets/2026-10-02-frontier-review/offline-nodes2.png),
[cancelled generation](research/assets/2026-10-02-frontier-review/cancelled-classic.png),
[App Mode](research/assets/2026-10-02-frontier-review/app-mode-complete.png), and
[router completion](research/assets/2026-10-02-frontier-review/router-complete.png).
The Project KB retains private JSON state/history, exact harnesses, review probes
and native runtime logs under `evidence/2026-10-02/`.

## Limits and release gate

- Gemma 270M/1B recognition is proved through authoritative metadata fixtures;
  those model weights were not downloaded or run.
- The owned HTTP response transport bounds socket reads. Arbitrary injected
  sessions retain their own blocking-I/O contract. System DNS resolution retains
  requests/platform behavior and is not made interruptible by this change.
- No new runtime dependencies or public nodes are introduced.
- Maintainer hands-on acceptance, master promotion, tagging and publication
  remain ENG-88. This validation does not supply that approval.

Rollback uses focused Git reverts or the pre-review dev commit. The maintained
Windows clone must be left clean at the final validated dev head, with owned
test processes stopped. The actual test processes and listeners on 8188/18080
are stopped; the agent-owned browser session is also stopped. The parent reviewed
the full Git diff against `2d1d5ce`. The maintained clone follows published `dev`;
final readback after the evidence-only closeout is recorded in ENG-87 and the
Project KB. No merge, tag or Registry publication was performed.

## Verification commands

Source and distribution gates:

```bash
.venv/bin/python -m pytest -q
npm test
.venv/bin/ruff check .
.venv/bin/ruff format --check .
uv sync --locked --extra dev --dry-run
uv build --out-dir /tmp/comfyui-llamacpp-october-release-check
.venv/bin/python tests/check_distribution.py /tmp/comfyui-llamacpp-october-release-check
.venv/bin/python tests/smoke_distribution.py /tmp/comfyui-llamacpp-october-release-check --prove-rejection
uvx twine check /tmp/comfyui-llamacpp-october-release-check/*
uv run --no-project --with requests==2.31.0 --with urllib3==1.26.20 --with pytest python -m pytest -q tests/test_http_deadlines.py tests/test_client_contract.py tests/test_streaming.py
COMFY_NO_TELEMETRY=1 uvx --from comfy-cli==1.12.0 comfy node validate
uvx pip-audit --requirement requirements.txt --progress-spinner off --strict
git diff --check
git diff 2d1d5ced6355bdb49e91f72b6ec9b89e93c087d2 HEAD
```

The native Windows suite used the existing Comfy Python and isolated declared
test dependencies, without changing the Comfy environment:

```bash
/mnt/c/ComfyUI/venv/Scripts/python.exe -c 'import sys; sys.path.insert(0, r"C:\Users\vi7or\AppData\Local\Temp\comfyui-llamacpp-review-20261002\pytest"); import pytest; raise SystemExit(pytest.main(["-q"]))'
```

Real workflow runs used the retained `comfyui_llamacpp_live_review.py` with stages
`direct`, `vision`, `text-only`, `router`, `diffusion`, `free`, and `stop` against
the isolated Comfy instance. Browser scripts ran through the installed Playwright
CLI in the stable `comfyui-llamacpp-review` session. Exact scripts and results are
in the private evidence directory referenced above.
