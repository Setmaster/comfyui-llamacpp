# Canonical Generate Validation

Date: 2026-07-12

This report records the post-0.3 Work Block B validation. It is separate from
the accepted [0.3 report](validation-0.3.md), whose immutable tag remains the
rollback baseline.

## Candidate identity

- Branch: `dev`
- Validated executable and package revision:
  `e22518094af183e8f311dedda0739ebeb42f8526`
- Package version: `0.4.0` candidate
- Stable rollback: tag `0.3.0` at
  `365986af4a47426b5513b3cee917ebec93a4204a`
- Stable branch: `master` and `origin/master` at
  `40ff5d730dc27cb51d68ae53142cab9b4c91f5af`
- Registry publication: not performed

Documentation and checklist closeout after `e225180` does not change executable
or packaged runtime inputs. The exact final repository and installed-clone SHA
is recorded in the Project KB closeout and final handoff.

## Test environment

| Component | Validated value |
| --- | --- |
| ComfyUI | 0.27.0 |
| Frontend | 1.45.20 |
| Comfy Python | 3.13.7 on Windows 11 |
| PyTorch | 2.9.1+cu130 |
| GPU | NVIDIA GeForce RTX 5090 |
| Driver | 591.86 |
| llama.cpp | Official Windows CUDA b9957, commit `c4ae9a88f` |
| llama-server | `C:\llama\llama-server.exe` |
| Direct text model | Qwen3.5 4B Q5_K_M GGUF |
| Router models | Qwen3-VL 4B Q8_0 and Qwen3-VL 8B Q6_K GGUF bundles |
| VLM model | Qwen3-VL 4B Q5_K_M with its f16 projector |
| Diffusion handoff | SDXL checkpoint at 512 by 512, 4 steps |

## Automated gates

The following source-tree commands passed:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q
node --test tests/js/*.test.mjs
.venv/bin/ruff check .
.venv/bin/ruff format --check .
find web tests/js -type f \( -name '*.js' -o -name '*.mjs' \) -print0 \
  | xargs -0 -n1 node --check
git diff --check
```

Results:

- Python: 779 tests and 54 subtests passed.
- Frontend: 56 tests passed.
- Ruff, format, JavaScript syntax, and whitespace checks passed. Ruff reported
  all 72 Python files already formatted.
- Immutable 0.2.1 and complete 0.3 node, schema, behavior, and workflow fixtures
  remained green.
- The focused router identity suite covers target mismatch, Windows drive and
  UNC normalization, POSIX case sensitivity, malformed local selections,
  inconsistent launch and preset evidence, complete active-root anchoring,
  matching basenames in different directories, safe contained symlinks, symlink
  escapes, bounded metadata, NUL rejection, compatibility fallback, timeout
  forwarding, and path-redacted failures.

The package and Registry-validation commands passed:

```bash
uv build --out-dir /tmp/comfyui-llamacpp-0.4.0-final-e225180
uvx twine check /tmp/comfyui-llamacpp-0.4.0-final-e225180/*
.venv/bin/python tests/check_distribution.py \
  /tmp/comfyui-llamacpp-0.4.0-final-e225180
COMFY_NO_TELEMETRY=1 uvx --from comfy-cli==1.12.0 comfy node validate
uvx pip-audit --requirement requirements.txt --progress-spinner off --strict
```

Results:

- The wheel and source archive passed metadata validation.
- Distribution manifests contained 17 workflow assets and 35 source-test files,
  with no duplicate archive members. The 17 assets are 11 workflow JSON files
  and six JPEG thumbnails.
- Registry configuration and security validation passed without publishing.
- `pip-audit` reported no known dependency vulnerabilities.
- The validated wheel SHA-256 was
  `b1a441f0815887c0b063d57976a91fd64d7c3c5f428cb90d69f2b541ddd0b5b7`.
- The validated source archive SHA-256 was
  `f401e0c7c52770fb461786dc5952ea5ea98e674e105b81a8efa6937d0f337702`.

Fresh extraction also passed:

```bash
unzip -q comfyui_llamacpp-0.4.0-py3-none-any.whl -d "$WHEEL_ROOT"
tar -xzf comfyui_llamacpp-0.4.0.tar.gz -C "$SDIST_ROOT"
PYTHONPATH="$WHEEL_ROOT" .venv/bin/python -c \
  'import comfyui_llamacpp; print(comfyui_llamacpp.__version__)'
cd "$SDIST_ROOT/comfyui_llamacpp-0.4.0"
/home/vi7or/Projects/comfyui-llamacpp/.venv/bin/python -m pytest -q
node --test tests/js/*.test.mjs
/home/vi7or/Projects/comfyui-llamacpp/.venv/bin/ruff check .
/home/vi7or/Projects/comfyui-llamacpp/.venv/bin/ruff format --check .
```

The extracted wheel reported version 0.4.0, registered 19 nodes, and contained
17 workflow assets. The extracted source archive repeated all 779 Python tests,
54 subtests, and 56 frontend tests, then passed Ruff and formatting.

## Windows byte-exact checkout

GitHub Actions initially exposed a checkout-only failure: Windows converted the
seven immutable 0.3 workflow JSON files to CRLF, so their byte hashes differed
although their content had not changed. Revision `d874121` adds a narrow
`example_workflows/*.json text eol=lf` rule.

This local simulation reproduced the Windows checkout policy and retained all
seven expected hashes:

```bash
CHECKOUT=$(mktemp -d /tmp/comfyui-llamacpp-autocrlf.XXXXXX)
git -c core.autocrlf=true checkout-index --all --force --prefix="$CHECKOUT/"
sha256sum "$CHECKOUT"/example_workflows/*.json
git check-attr text eol -- example_workflows/direct-text.json
```

The attribute check returned `text: set` and `eol: lf`. The succeeding
intermediate GitHub Actions run `29194744692` at `d874121` passed 729 Windows
tests, skipped 44 platform-specific tests, and passed 54 subtests.

## Current Comfy browser gate

The maintained Windows clone was clean at exact executable revision `e225180`,
loaded package 0.4.0, and registered all 19 nodes before the final browser and
runtime gates.

The Playwright gate loaded all four canonical workflows in both classic and
Nodes 2.0 renderers:

- Canonical Text
- Canonical VLM Image Understanding
- Canonical Structured JSON
- Canonical App Mode

The exact-revision 12-check browser gate covered node creation through search,
all seven dynamic samplers, image counts and socket ordering at 0, 1, and 10,
per-user profile refresh, explicit snapshot update, undo and redo,
missing-model retention, clone state isolation, transient serialization, graph
reload, both renderers,
and raw numeric App Mode node IDs.

The exact-revision App Mode contract retained ten intended inputs, including
read-only **Generation Status** and **Live Response**, and one native Generate
output. The two live fields had `serialize=false`; Generate retained 25 backend
widget values.

A preceding live direct run at `bf7b48a` used the same App Mode and direct
generation source present in `e225180`. Prompt
`83757e7e-e808-4e4b-aa20-c3798e66ab54` returned exactly
`APP_MODE_TRANSIENT_OK`, exposed `complete (1.1s, prompt 100.0%, release
complete)`, and stored native history output under node 3. Changes after that
run were limited to workflow line endings and router identity validation.

Frontend 1.45.20 retains terminal text in native jobs and history but does not
render inline text in App Mode's central result pane. The canonical live fields
are therefore current-session feedback. They reset on reload and are not
serialized or written as surrogate output files.

The four canonical JSON workflows each have a same-stem RGB JPEG thumbnail at
768 by 768. Setup Check and Quick Text retain their two existing thumbnails.
All six passed the bounded image and exact-stem tests, and the four new images
were visually inspected after capture from the current frontend.

## Real direct, structured, and cancellation gate

The live App Mode run above repeated exact direct generation and terminal
release. The broader direct gate on predecessor `b822d48` used the same direct
runtime, stream, and Generate source present in `e225180`; the intervening
executable changes are restricted to router identity validation.

That gate proved:

- retained direct generation returned `DIRECT_RETAINED_OK`;
- an empty URL reused the same runtime and returned `DIRECT_REUSE_OK`;
- both used runtime epoch 13;
- exact Stop was labelled **Stop generation**, targeted the private generation,
  produced a categorized cancelled result, retained a seven-character partial,
  attempted stream cleanup twice, and received positive DELETE confirmation;
- the owned direct runtime remained healthy after cancellation;
- driver-visible use rose to about 11,055 MiB from an observation near
  6,865 MiB while the model was resident.

The structured and error gate returned a valid object whose summary began
`Local inference ensures data privacy` and whose keyword array was exactly
`["privacy", "data", "security", "local", "device"]`.

A real Token Ban node was connected to Generate and the banned token `cloud` did
not appear. An impossible schema reached categorized `[timeout]` after its 300
second deadline. An invalid explicit URL reached categorized `[transport]`
without disturbing the healthy managed direct runtime at epoch 13.

## Real VLM gate

The VLM and downstream diffusion gates below were captured in the same
`b822d48` runtime session. Changes through `e225180` did not alter direct/VLM
generation or diffusion execution; they added router identity anchoring,
documentation, assets, tests, and the LF checkout rule.

The VLM gate used actual image pixels from
`C:\ComfyUI\input\1767969154869353.png`, model
`qwen3-vl-4b-instruct-bakeoff/Qwen_Qwen3-VL-4B-Instruct-Q5_K_M.gguf`, and
projector `mmproj-Qwen_Qwen3-VL-4B-Instruct-f16.gguf`. Prompt ID prefix
`547424d8` returned:

> The central character, a stylized anime girl with light blue hair, wears a
> geometric, multi-colored outfit against a swirling background of red and
> purple.

The graph proved one real image link and `image_amount=1`. Release converged to
no owned runtime. Driver-visible use peaked near 12,348 MiB and returned to about
6,499 MiB after release.

## Real router gate

The final installed-tree router gate used two resident-capable bundles:

1. Model B, `qwen-vl-8B-Instruct`, was loaded first.
2. Model A, `qwen-vl-4B-Instruct`, generated exactly
   `ROUTER_SCOPED_RELEASE_OK` under prompt
   `d4243742-e437-4386-9557-3d90589a3099`.
3. Scoped release unloaded only A while B remained loaded.
4. Selecting local Q5
   `qwen3.5-4b-bakeoff/Qwen_Qwen3.5-4B-Q5_K_M.gguf` resolved the directory ID
   whose router target metadata actually named Q6. Prompt
   `27f96ff1-0404-461d-b32b-7181f8284635` failed before generation as
   `[model_missing]`. The Q6 child remained unloaded and B remained loaded.
5. Native `POST /free` returned HTTP 200 with
   `X-ComfyUI-LlamaCpp-Release: complete`, unloaded B, and retained the empty
   owned router.
6. Explicit Stop removed the router and returned discovery to `mode=none`,
   `owned=false` under prompt `703f9472-d0e3-4733-976f-110a8e598017`.

No llama-server process remained after this gate. The earlier two-model run
observed about 19,660 MiB at peak. After the final Comfy restart and router/App
Mode tests, the new Comfy process baseline stabilized near 8,685 MiB with no
llama-server process; that post-restart value is not compared directly to the
older-process baseline.

## Downstream diffusion allocation

Without restarting ComfyUI between LLM teardown and the downstream workload, a
standard SDXL graph completed at 512 by 512 and 4 steps. Prompt ID prefix
`0842467a` wrote
`C:\ComfyUI\output\llamacpp_wbb_handoff_00001_.png`. Driver-visible use peaked
near 11,951 MiB and returned to about 5,739 MiB in that process generation.

This is functional downstream allocation evidence, not an image-quality claim.
The low-step output was visually inspected only to confirm a valid diffusion
result. LLM discovery stayed `mode=none`, `owned=false` before and after.

## Independent review

The integrated reviews found malformed GGUF-path bypass, platform-sensitive
normalization, unbounded aggregate metadata, suffix-only target ambiguity, and
a safe contained-symlink compatibility regression. Every finding was fixed and
covered by focused tests before the final live router gate.

The blocking fresh-eyes re-review inspected the exact final implementation,
independently repeated the active-root collision, safe-symlink, and escape
cases, and reported P0 none, P1 none, and P2 none. It confirmed that ordinary
catalog callers retain resolved paths, only managed router identity uses lexical
anchoring, aliases cannot bypass anchoring, and metadata-free older routers
retain compatibility.

## CI and installed clone

GitHub Actions run `29195799658` passed all seven jobs at exact revision
`e22518094af183e8f311dedda0739ebeb42f8526`:

- Linux Python 3.10, 3.11, 3.12, 3.13, and 3.14
- Windows Python 3.13
- quality, package, and frontend validation

Each Linux job passed 779 tests and 54 subtests. Windows passed 735 tests,
skipped 44 platform-specific tests, and passed 54 subtests. The quality job
repeated package, distribution, Ruff, format, and frontend validation.

The maintained clone at
`C:\ComfyUI\custom_nodes\comfyui-llamacpp` was clean on `dev` at the same
revision after the final live tests. Closeout documentation commits contain no
runtime changes; their exact final clone and CI state is recorded in the Project
KB and final handoff.

## Remaining boundaries

- The user's hands-on acceptance and any merge to `master` remain a human gate.
- Version 0.4.0 has not been published to the Registry.
- App Mode central result rendering depends on a Comfy frontend behavior outside
  this pack. Native history output and the transient visible fields are both
  preserved.
- Router target proof applies when current status records expose launch or preset
  metadata. Older routers retain ID-only compatibility, so one base GGUF per
  bundle remains the portable rule.
- Exact cancellation depends on llama.cpp's capability-probed internal
  resumable-stream interface. Unsupported or Unknown endpoints retain the
  truthful whole-Comfy-job fallback.
- Terminal process or model state does not promise instantaneous driver-memory
  accounting. The downstream allocation is the decisive handoff check.
- No live macOS or BSD runtime was available for this work block.

## Conclusion

The validated candidate provides one strict, local-only text, vision, prompt,
and structured generation surface without changing the 17 released nodes. Its
direct and router release promises reached terminal state, exact cancellation
was truthful, current router target mismatch failed closed, current Comfy classic
and Nodes 2.0 passed, package and Registry validation passed, and all seven CI
jobs passed. Promotion still waits for the maintainer's hands-on acceptance.
