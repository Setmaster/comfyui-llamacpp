# 0.3 validation report

Date: 2026-07-10
Validated runtime revision: `9f83be46dbe2986600aa46d3890590931dffe26b`
on `dev`

Cross-platform test and closeout revision:
`fc3e25220cf53f5f86ff458afcdeb94659d35ca3` on `dev`. Its runtime source is
identical to `9f83be4`; it makes two kernel-dependent pidfd tests deterministic
and archives the completed change bundle.

This report records the automated and live evidence gathered before handing
0.3 to the maintainer for final user acceptance. It is not a substitute for
the maintainer's checklist in [user acceptance](user-acceptance.md).

## Test environment

| Component | Validated value |
| --- | --- |
| ComfyUI | 0.27.0 |
| Frontend | 1.45.20 |
| Comfy Python | 3.13.7 on Windows 11 |
| PyTorch | 2.9.1+cu130 |
| GPU | NVIDIA GeForce RTX 5090 |
| Driver | 591.86 |
| Broad validation llama.cpp | Official Windows CUDA release b9957 |
| Final direct-smoke llama.cpp | Windows CUDA build 8261 (`e22cd0aa1`) |
| Direct text model | Qwen3.5 4B Q5_K_M GGUF |
| Router and VLM model | Qwen3-VL 4B Q8_0 GGUF with matching Q8_0 projector |

## Automated evidence

The following commands passed from the repository root:

```bash
uv run python -m pytest -q
uv run ruff check .
uv run ruff format --check .
node --test tests/js/*.test.mjs
for file in web/*.js; do node --check "$file"; done
git diff --check
uv build
uvx --from comfy-cli comfy node validate
```

Results:

- Linux: 382 tests and 36 subtests passed.
- Native Windows Python 3.13.7: 338 tests and 36 subtests passed, with 44
  POSIX or Linux-specific process tests skipped.
- Frontend: 10 tests passed, and every shipped JavaScript file passed syntax
  checking.
- Ruff, formatting, and whitespace checks passed.
- The sdist and wheel built successfully. A clean Python 3.13 environment
  installed the wheel, imported version 0.3.0, found the packaged frontend and
  Linux supervisor, and registered all 17 nodes.
- The current Comfy Registry validator passed all configuration and security
  checks without warnings.
- GitHub Actions run `29135583640` passed all seven jobs for `fc3e252`: Python
  3.10 through 3.14 on Linux, Python 3.13 on Windows, and the quality/package
  job. An earlier run exposed two tests that depended on the local pre-6.9
  kernel fallback. The test-only successor explicitly selects the fallback in
  those cases while preserving separate atomic-success, checked-fallback, and
  no-fallback coverage.
- The 150-case process suite covers launch-time pidfd generation authority,
  `P_PIDFD` observation and consumption, exact single-process signaling,
  Linux 6.9 atomic group signaling, checked older-kernel fallback, external
  reaping, PID and PGID reuse, provisional retries, Windows Job behavior,
  stop/monitor races, and descriptor ownership. Twenty complete repetitions
  passed, for 3,000 process-test executions.
- The same process suite passed under Python 3.11. Twenty warmed real
  start/stop cycles returned to the exact file-descriptor baseline.
- Streaming log sanitization passed exhaustive 2, 3, and 4-byte UTF-8 split
  positions, every configured-literal flush offset, 600 randomized Unicode,
  multiline, and credential chunk cases, long-whitespace bounds, full
  Authorization schemes, and cross-generation drain isolation.
- `pip-audit` reported no known dependency vulnerabilities in the locked test
  environment.

## Real ComfyUI evidence

The live checks used Comfy's `/prompt`, `/history`, `/free`, and plugin runtime
routes. They executed the actual nodes rather than calling their Python methods
directly.

| Area | Result |
| --- | --- |
| Startup and schema | Comfy loaded 0.3.0, registered 17 nodes, exposed all ten VLM image inputs, 24 direct GGUF models, and 8 projectors from configured roots. |
| Direct mode | The owned b9957 server generated the requested exact text. Token Count returned pieces, and Model Info returned live properties. |
| Structured output | A nested strict JSON Schema returned `{"status":"pass","count":3}`. |
| Router mode | `(auto)` selected the populated configured root. b9957 exposed 14 native presets, and load, exact text generation, unload, and catalog reload reached their terminal states. |
| VLM | ADV++ Image2Prompt described the supplied image accurately through the real image input and matching projector. |
| Attached endpoint | Generation through a separately started local server succeeded. Native Comfy free returned a no-op for this pack, and the external server remained healthy. |
| Deferred release | Native free during an active managed generation returned `deferred`. The process stayed alive until generation completed, then the queued release completed and removed it. |
| Explicit controls | Release Runtime, Unload Model, and Stop Server retained their independent terminal behavior. |

The hardened runtime revision `9f83be4` also passed a fresh real-Comfy direct
smoke with the same Comfy, frontend, Python, GPU, driver, and model environment,
using llama.cpp build 8261 (`e22cd0aa1`). It generated `FINAL 9F83BE4 PASS`
through an assigned Windows Job. Native `/free` then returned
`X-ComfyUI-LlamaCpp-Release: complete`, removed PID 166876, reported no
remaining descendants, and returned runtime status to `mode=none`,
`lifecycle=idle`. The stop completed without escalation.

## Native Comfy unload and GPU handoff

Direct native free returned `X-ComfyUI-LlamaCpp-Release: complete`, removed the
owned process tree, and changed runtime status to idle. In one direct run,
driver-visible use fell from about 12,579 MiB to 8,315 MiB after release.

Router native free unloaded the resident child to a nonresident state while
keeping the owned router process alive. Driver-visible use fell from about
15,127 MiB to 9,352 MiB in that run.

The full shared-GPU test passed without restarting ComfyUI:

1. A 256 by 256 diffusion graph completed and used about 12,361 MiB.
2. Direct LLM startup with `unload_comfy_models_before_start=true` evicted the
   Comfy-managed allocation and generated successfully at about 9,862 MiB.
3. Native free completed, removed the owned LLM process, and memory converged
   to about 5,737 MiB.
4. The same diffusion graph then completed again and reallocated to about
   12,361 MiB.

The second diffusion execution is the decisive functional reallocation check.
The memory values are observations from this workstation, not portable
thresholds.

## Concurrency and abrupt ownership

The status endpoint was probed during the exact startup transition that
previously blocked it. While startup owned the lifecycle operation lock, the
route returned in 1.863 ms with a valid transitional snapshot:

```text
mode=none lifecycle=idle process=running pid=152808 windows_job_assigned=true
```

The workflow then completed with the exact requested response and changed to
`mode=direct`, `lifecycle=ready`.

For abrupt Windows ownership validation, only the real Comfy Python process
was force-terminated. No recursive process-tree kill was used. Closing that
process closed the assigned kill-on-close Job Object, and the recorded
llama-server child disappeared within the bounded polling window. The Comfy
port also closed, and no validation llama-server remained.

## Compatibility evidence

- Every released 0.2.1 class ID, function name, output tuple, socket name,
  default, and legacy positional widget prefix is covered by contract tests.
- Historical workflow fixtures load without positional reinterpretation.
- Dynamic image inputs round-trip through the real current frontend at counts
  0, 1, and 10.
- Five connected examples load through Comfy's graph path and are covered by
  structural and portability tests.
- Root compatibility modules remain importable facades over the refactored
  implementation.

## Known boundaries

- llama.cpp's native router scans one selected models root. Root GGUF files and
  one logical model in each immediate child directory are visible. Deeper
  models are invisible, and directories with multiple base models or multiple
  projectors are ambiguous. Direct-mode discovery remains recursive and can
  therefore show more models than the active router. Treat **List Models** as
  the authoritative router catalog.
- Router reload detects catalog and preset changes, but not replacement bytes
  at the same GGUF path. Explicitly unload and load that model, or restart the
  router, after an in-place replacement.
- Windows abrupt cleanup requires `windows_job_assigned=true`. The reported
  descendant fallback supports deterministic ordinary stop but cannot provide
  the same abrupt-owner guarantee.
- Linux requires functional `pidfd_open` and `waitid(P_PIDFD)`, normally Linux
  5.4 or newer. Unsupported hosts fail before a server is spawned. Linux also
  has parent-death supervision. Other POSIX systems have exact ordinary
  process-group cleanup but no equivalent abrupt-owner claim.
- Linux 6.9 or newer provides atomic pidfd process-group signaling. The source
  and deterministic unit path were validated, but the available WSL kernel was
  6.6 and could not live-test that flag. Older kernels require a fresh complete
  authority proof followed by `killpg`, leaving a narrow race only if unrelated
  code reaps this controller's child in that final interval.
- `Popen` and `pidfd_open` are separate operations. A foreign child waiter or a
  post-spawn resource failure in that acquisition interval can leave visible
  incomplete ownership until the Linux parent-death supervisor observes owner
  exit. Eliminating that boundary requires an atomic pidfd spawn primitive.
- Real macOS and BSD lifecycle execution was not available for this validation.
- Driver telemetry can converge after a terminal process or router state.
  Functional allocation by the next GPU workload remains the final proof.
- The package has Registry-oriented metadata but has not been published to the
  Comfy Registry. Publishing and any merge to `master` are outside this
  validation handoff.

## Maintainer handoff

Run [the user acceptance checklist](user-acceptance.md) from the final remote
`dev` revision. Record any differences in your own models, workflows, OS, and
GPU environment. The branch should be merged only after that hands-on pass.
