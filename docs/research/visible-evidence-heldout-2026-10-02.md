# Visible Evidence held-out decision, 2026-10-02

Status: completed; **defer Visible Evidence**. No bundled profile or default
change, and no automatic additional round.
Tracking: ENG-95. Installed checkout: `5a5f2894c83a42e787e15a975bd814586f3f7759`.
Running plugin code: `612bdad1f85fdb3a19dd5f63533eaaddd55e41fc`; the intervening
change affected a test fixture, with runtime/profile sources unchanged.
This bounded evaluation does not promote `master`, publish a release, or change
the deferred ENG-88 maintainer acceptance gate. The frozen protocol below is
retained, followed by the measured result and scoring provenance.

## Why this follow-up exists

The completed ENG-89 artifact at
`/tmp/comfyui-profile-bakeoff-20261002-fixed/summary.json` reports mean paired
quality deltas of -1.0 for Prompt Fidelity and +2.0 for Visible Evidence. Text
remains deferred. Vision qualified for one follow-up, but all original OCR
responses were wrong. The earlier synthetic tasks do not establish general
vision quality or reliable text recognition.

This experiment retains the exact `visible-evidence-v1` snapshot and shipped
Freeform baseline. It introduces three new tasks and a second installed model.
The candidate was chosen after observing the original experiment, so these are
held-out tasks, not an independently chosen profile or a statistical replication.
No prompts, profiles, scores or gates may be tuned after these outputs are seen.
There is no automatic third round.

Original evidence retained in every prepared `experiment.json`:

| Artifact | SHA-256 |
| --- | --- |
| Original plan content hash | `372fbdc0e69c33cededa29e776f1ab04401744372aad616a19ed128f49b5d887` |
| Original `results.jsonl` bytes | `9fe0f6390b334043acadfb8f531a9a34e952c7ddb3748312f7833eddd6ddde6b` |
| Original `summary.json` bytes | `e6df26866a36766e04bcb227fec10207709197558b0ddc3806bce46b093f2e1a` |
| Original protocol bytes inspected for this design | `7fe4ef6a1e6a6c042b67b29c91c56cd842ea9b5357de25921962c305b16e5ce9` |

Preparation records original plan-file bytes separately from its content hash
and copies the inspected protocol to `prior-protocol.md`. That frozen copy and
the original run files remain untouched. Its pending-results paragraph is
historical: the current ENG-89 report was subsequently updated with results.
The protocol hash above identifies the preserved copy, not the later report.

## Frozen tasks, controls and budget

The source files are `tests/benchmarks/task_profiles/heldout_cases.json`,
`heldout.py`, and `heldout_test.py`. Images are repo-authored deterministic
800 by 600 RGB drawings made with the existing Pillow dependency. There are no
external pictures, font files, downloads or model-generated assets. Integer
geometry, large seven-segment digits, PNG hashes and raw RGB hashes make the
facts independently inspectable. The author inspected all three generated
images before collecting output; an independent reviewer also inspected them
and passed the pre-live gate with no P0/P1/P2 findings.

| Task | Complete requested evidence | Frozen facts |
| --- | --- | --- |
| `heldout-symbol-grid` | Count orange diamonds and blue circles, distinguish filled/outlined forms, and describe each row from left to right. | Three orange diamonds, two filled and one outlined; three blue circles, one filled and two outlined. Top: filled diamond, filled diamond, outlined circle. Bottom: outlined diamond, filled circle, outlined circle. |
| `heldout-digit-bars` | Transcribe three digit labels, identify bar colors, rank heights, and say whether a numeric measurement scale is shown. | Digits `3`, `7`, `2`; left purple bar shortest, middle teal bar tallest, right gold bar intermediate; no numeric scale or units. Labels are not asserted bar measurements. |
| `heldout-screened-paths` | Describe line pieces and covering rectangles, compare alignment, and assess whether hidden connections can be established. | Left panel: two orange pieces at the same height. Right panel: right orange piece lower. Both have a central gray vertical rectangle; neither establishes a hidden connection or disconnection. |

Every prompt includes its complete scored output restrictions: one paragraph,
no heading/markdown/commentary, its exact word limit, and its relevant inference
exclusions. Facts and scoring descriptions never enter submitted graphs.
The screened-path case supplies a nonempty operator system prompt, identical in
both arms and preserved byte-for-byte. Candidate system instructions apply only
where the operator system is empty. Candidate snapshots cannot change models,
images, sampling, deadlines, release controls or output limits.

The frozen models are:

- Qwen3-VL 4B Q5:
  `qwen3-vl-4b-instruct-bakeoff/Qwen_Qwen3-VL-4B-Instruct-Q5_K_M.gguf`,
  using its existing automatically resolved Q8 projector.
- MiniCPM-V 4.5 Q5:
  `minicpm-v-4_5-bakeoff/MiniCPM-V-4_5-Q5_K_M.gguf`,
  using explicit `minicpm-v-4_5-bakeoff/mmproj-model-f16.gguf`.

Each model receives all three tasks at seeds 17 and 29 with both arms:
12 measured generations per model, **24 measured generations total**. Each
model first receives one excluded Freeform image warmup at seed 7, so the total
budget is **26 generations including warmups**. AB/BA order alternates by task,
seed and model. No extra measured tasks, extra seeds or profile variations are
authorized by this protocol.

Both models use a 4096-token launch context. Generate controls are copied from
the original frozen plan: Custom sampling, temperature 0.2, top P 0.9, top K 40,
min P 0.05, repetition penalty 1.0, presence/frequency penalties 0, Thinking Off,
256 output tokens, 120-second deadline, no prefix-cache request, no release, and
strict `raise_error` partial-output behavior. The entire input graph is paired;
only the profile snapshot changes. The different models retain their respective
compatible projectors and otherwise equivalent launch controls. These are
within-model profile comparisons, not an absolute speed ranking of models.

## Execution and evidence

Before freezing, verify both installed model/projector identities and hashes,
the exact plugin/Comfy/frontend revisions, llama.cpp build, GPU/driver, and
effective launch settings. Environment files follow the original harness README
schema. Each records `context_size: 4096`; the held-out runner enforces that
recorded value. Actual remote weight hashes and context settings require the
parent's host evidence, since passive discovery does not expose all of them.

Prepare both model plans together before generating any outputs:

```bash
.venv/bin/python tests/benchmarks/task_profiles/heldout.py prepare \
  /tmp/comfyui-visible-evidence-heldout-20261002 \
  --qwen-model qwen3-vl-4b-instruct-bakeoff/Qwen_Qwen3-VL-4B-Instruct-Q5_K_M.gguf \
  --secondary-model minicpm-v-4_5-bakeoff/MiniCPM-V-4_5-Q5_K_M.gguf \
  --prior /tmp/comfyui-profile-bakeoff-20261002-fixed
```

Inspect both sets of identical images and record the emitted experiment hash.
Every later command validates both plans, source hashes, gates and PNGs. This
includes the held-out runner, fixtures, original recorder, original profile
document and production profile transformation module. Any source edit after
freezing invalidates the experiment. The original recorder remains unchanged;
a scoped validator adapter accepts the held-out model plans' 13-run shape.

The parent starts one owned direct runtime at a time through normal Comfy
controls and verifies the intended model/projector. Keep each process resident
for its full 13-request batch. The harness never starts, stops or reconfigures a
runtime. With Qwen running, use:

```bash
.venv/bin/python tests/benchmarks/task_profiles/heldout.py run \
  /tmp/comfyui-visible-evidence-heldout-20261002 --model-key qwen \
  --server http://127.0.0.1:8188 \
  --environment /tmp/comfyui-heldout-qwen-environment.json
```

After the parent releases Qwen and starts the verified MiniCPM runtime, use:

```bash
.venv/bin/python tests/benchmarks/task_profiles/heldout.py run \
  /tmp/comfyui-visible-evidence-heldout-20261002 --model-key secondary \
  --server http://127.0.0.1:8188 \
  --environment /tmp/comfyui-heldout-minicpm-environment.json
```

The unchanged recorder uploads only generated images, requires an idle queue,
checks model/alias, epoch and projector continuity before/after each request,
and retains exact native graphs, prompt IDs, histories, responses and latency.
It stops on the first failure without retrying or issuing a global interrupt.
Inspect any unfinished prompt before other work. A failed or incomplete trial
cannot pass; document the failure and defer, rather than adding another round.

## Blinded scoring and fixed decision

Generate one combined 24-card scoring sheet only after both batches complete:

```bash
.venv/bin/python tests/benchmarks/task_profiles/heldout.py scorecards \
  /tmp/comfyui-visible-evidence-heldout-20261002
```

Give an independent scorer only the root `scorecards.json`, the three PNGs from
either model directory, and this protocol. The combined sheet omits model and
arm labels and shuffles all 24 responses. Keep experiment/model plans, raw
results, per-model sheets and every scoring key away until scoring is final.
The parent owns scoring isolation. If the scorer saw labels, record that
limitation instead of claiming blind review.

Use the original 0-to-10 rubric without changes:

`max(0, 4 * mean(fact scores) + 4 * mean(constraint passes) + clarity - min(4, 2 * unsupported additions))`

- Each listed fact scores 1 for correct/complete, 0.5 for recognizable but
  incomplete, or 0 for missing/contradicted/wrong. Cite the supporting span or
  missing fact. Synonyms and numerals written as words are acceptable when their
  meaning is exact. Digit recognition is scored per label, not keyword matching.
- Every explicit constraint is pass/fail with evidence. Whitespace token counts
  enforce the listed word limit; one-line detection only supplements semantic
  paragraph, markdown and commentary review. Automatic failure cannot be scored
  as a pass.
- Clarity is 2 for coherent and directly usable, 1 for minor editing needed, or
  0 for unusable/irrelevant/contradictory. Give a reason. Extra length has no bonus.
- `unsupported_claims` is a list of strings, each quoting a distinct added claim
  and explaining its absent evidence. An empty reviewed list means no additions.
  A wrong listed fact loses fact credit; do not penalize that same error again
  as an added claim. An invented detail may also violate an explicit exclusion.
- Mark every card `reviewed: true` after checking facts against the actual image.
  All evidence fields are required. Failed samples score zero; incomplete
  batches are rejected from aggregate scoring and must be reported as deferred.

After independent scores are final:

```bash
.venv/bin/python tests/benchmarks/task_profiles/heldout.py summarize \
  /tmp/comfyui-visible-evidence-heldout-20261002
```

The final recommendation is **ship** only if every frozen gate passes:

1. Neither model's mean paired quality delta is negative.
2. No task's mean paired delta within either model is below -0.5 points. This
   checks all six model/task combinations, preventing a strong model from
   masking a task regression on the other model.
3. Combined mean paired quality gain is at least 0.5 points out of 10. Both
   models have six pairs, so they receive equal weight.
4. Every candidate constraint passes and all measured outputs complete.
5. Candidate unsupported-claim count is no greater than Freeform's within each
   model, counting distinct asserted additions per response.
6. Each model's median paired latency ratio is at most 1.25.

Otherwise the final recommendation is **defer**. There is no automatic further
evaluation round. Execution timestamps are used within a model when all its
measured records contain valid values; otherwise wall time is used throughout
that model. Warmups and initial model loading are excluded. Report each model's
timing basis, task deltas, constraint failures, unsupported additions, and digit
accuracy even when the overall gates pass. These thresholds do not guarantee
perfect OCR. The summary reports exact digit-fact accuracy separately so a
quality gain cannot be presented as a proven OCR repair.

A passing result supports offering the unchanged profile for these constrained
vision tasks in the development candidate. It does not establish broad natural
photo, language, model-family or OCR reliability, and does not replace Freeform
as the default. The runner only writes a recommendation; the parent owns any
scoped shipping implementation and verification. Two seeds per task provide
diagnostic evidence, not statistical significance.

## Results and final decision

**Defer Visible Evidence.** All 24 measured generations and both excluded
warmups completed. Mean paired quality declined on both models: -0.04445 points
on Qwen and -1.23333 on MiniCPM, with a combined mean of -0.63889. Three frozen
quality gates failed. No bundled profile or Freeform default changes follow
this experiment, and no automatic further evaluation round is authorized.

The exact experiment content hash is
`a50d91fcf4d97df88d793cab815f099ab21d8dab6390e751fcb5ce599ef14906`.
Both model plans, source hashes, image hashes, input controls and decision
thresholds remained frozen. Each model retained one owned direct process,
model selection and projector throughout its batch; before/after identities
match on all 26 requests. All measured histories provide execution timestamps,
so both models use `execution_ms` for their paired ratios.

| Model | Freeform mean / 10 | Candidate mean / 10 | Mean paired delta | Median paired latency ratio |
| --- | ---: | ---: | ---: | ---: |
| Qwen3-VL 4B Q5 | 7.46667 | 7.42222 | -0.04445 | 1.06171 |
| MiniCPM-V 4.5 Q5 | 8.86667 | 7.63333 | -1.23333 | 1.01632 |
| Combined, equal model weights | 8.16667 | 7.52778 | -0.63889 | Not pooled |

These are composite rubric scores, not task success rates. Latency ratios are
candidate/Freeform for each matched task and seed, then summarized by the
median within each model. They are not ratios of pooled execution-time medians.

| Task | Qwen mean paired delta | MiniCPM mean paired delta |
| --- | ---: | ---: |
| Filled/outlined symbol grid | +0.33335 | -3.00000 |
| Digit labels and bar heights | +0.33330 | 0.00000 |
| Screened paths and unknown connections | -0.80000 | -0.70000 |

| Frozen gate | Observed result |
| --- | --- |
| Neither model's mean regresses | Fail: both means are negative. |
| No model/task mean below -0.5 | Fail: Qwen paths -0.8; MiniCPM grid -3.0 and paths -0.7. |
| Combined mean gain at least +0.5 | Fail: -0.63889. |
| All candidate constraints pass | Pass: all 12 candidate responses pass all three listed constraints. |
| Candidate unsupported claims no greater than Freeform per model | Pass: zero separately penalized unsupported additions in every arm/model. |
| Median paired latency ratio at most 1.25 per model | Pass: 1.06171 and 1.01632. |
| All measured outputs complete | Pass: 24/24; both excluded warmups also completed. |

Passing output restrictions and receiving no separate unsupported-addition
penalty did not make the responses factually correct. Under the frozen rubric,
wrong listed facts lose fact credit rather than also being counted as added
claims. For example, MiniCPM's candidate responses said there were two orange
diamonds and two blue circles, then listed three of each across the two rows.
This combination lost count/fill facts and clarity for its internal
contradiction. Qwen's candidate often denied the visible distinction between
filled and outlined shapes. Both models also produced incomplete or incorrect
path-alignment descriptions, contributing to the failed task-regression gate.

Qwen read the middle digit `7` as `1` in all four measured digit responses.
Both Qwen arms therefore scored 4/6 fully correct digit facts. MiniCPM read
all three labels correctly at both seeds in both arms, scoring 6/6 each. The
Qwen candidate's small composite improvement on this task came from a better
height ranking at one seed, not improved OCR. No OCR repair is demonstrated.

## Scoring provenance and public evidence

Three independent Codex scorers each reviewed one task's eight responses,
including both models and both seeds, without model or arm labels. They checked
the actual PNG and supplied evidence for every fact, constraint and clarity
score. The parent reviewed each card before unblinding. This was model-based
assessment, not a human rating panel.

During that blinded review, the paths scorer revised two clarity scores from
1 to 2: the responses were coherent, and their factual errors were already
deducted from fact scores. Four other path cards received rationale-only edits
clarifying independent wording defects. No fact scores, constraints, responses,
profiles or thresholds changed. The original sheet remains preserved as
`paths-before-clarity-review.json`; final scorer-file hashes match recorded
provenance, and the merged scoring sheet matches those final files exactly.
The public JSON includes all six before/after clarity edits and identifies the
two numeric changes. Unblinding and aggregation occurred after these revisions.

The [public evidence JSON](assets/2026-10-02-backlog/heldout/results.json)
contains all 26 exact responses, all 24 final scorecards and totals, seeds,
profiles, complete task facts and constraints, fixed decision gates, measured
execution/wall latency, software/model/projector hashes, and scoring provenance.
It preserves hashes of both supplied environment files and their recorded
counterparts, along with the frozen source, plan, raw-results and scoring files.
Absolute paths, process/client identifiers, listener details and raw native
history bodies are omitted. Generated response and scoring text is unchanged.

Independently inspectable fixtures:
[symbol grid](assets/2026-10-02-backlog/heldout/heldout-symbol-grid.png),
[digit bars](assets/2026-10-02-backlog/heldout/heldout-digit-bars.png), and
[screened paths](assets/2026-10-02-backlog/heldout/heldout-screened-paths.png).
The two model directories contained byte-identical copies of each fixture.
Public PNG and RGB hashes match the frozen plans.

Both runs used llama.cpp `b9957 c4ae9a88f`, Comfy revision `8d534945`, frontend
`1.53.6`, Python `3.13.7`, PyTorch `2.9.1+cu130`, and an NVIDIA GeForce RTX 5090
with driver `610.88`. Both launch records specify context 4096, GPU layers 999,
main GPU 0 and batch size 512. Actual supplied and recorded environment values
agree; the live plugin revision and checkout revision are distinguished above.

| Weight file | SHA-256 |
| --- | --- |
| Qwen3-VL 4B Q5 model | `026ad07482ca4a1121349fbb7b77abf9b81c0cbc156e3cf52ee7bb8d0229cde4` |
| Qwen Q8 projector | `33d19545c921a784354b7cc099fa1f0e5b48352b73ab82bf077600e5ff9c6834` |
| MiniCPM-V 4.5 Q5 model | `25ef74e858803818d474f81835ce58309df3dbfa32a8ca6e01176e981bcd1614` |
| MiniCPM F16 projector | `7a7225a32e8d453aaa3d22d8c579b5bf833c253f784cdb05c99c9a76fd616df8` |

The exporter checked every public response, scorecard, score total and latency
against private source evidence, independently recomputed the paired aggregate
and decision, checked PNG/RGB hashes and environment agreement, scanned for
private paths and credentials, and confirmed the original evidence files were
unchanged. The final JSON SHA-256 is
`8c75392b07234f1a7ea56d7ef8d41f851ae9af1e9720af240c9579b6d8e16b60`.

The result remains limited to three synthetic diagnostics, two seeds and the
two recorded model/projector combinations. It does not establish natural-photo,
multilingual or general model-family behavior. Within this fixed decision scope,
the evidence supports deferral rather than bundling the candidate.

Offline verification:

```bash
.venv/bin/python -m pytest -q tests/benchmarks/task_profiles/heldout_test.py
.venv/bin/ruff check tests/benchmarks/task_profiles/heldout.py tests/benchmarks/task_profiles/heldout_test.py
.venv/bin/ruff format --check tests/benchmarks/task_profiles/heldout.py tests/benchmarks/task_profiles/heldout_test.py
```

Synthetic test scores exercise aggregation and gate boundaries only. They are
not live quality or latency measurements.
