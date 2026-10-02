# Task-profile bakeoff, 2026-10-02

Status: completed local bakeoff; Prompt Fidelity deferred, Visible Evidence
eligible for a separate follow-up. Neither candidate is shipped.
Tracking: ENG-89; follow-up ENG-95. Executed plugin revision: `f84e227878208b094ec8b0f9510f4d581e9960c6`.

## Question and scope

Does a small reusable prompt profile improve image-prompt rewriting or factual
image understanding over Freeform when both receive the same complete task?
Compare on one installed Qwen3-VL 4B Q5 model with its automatically resolved
Q8 projector and llama.cpp b9957. This is a bounded local experiment, not a
cross-model ranking or proof that a profile belongs in production defaults.

The inspected implementation in `generation/profiles.py` accepts only identity,
description, and three text fields. `apply_task_profile` wraps the prompt and
fills an exactly empty system prompt. A nonempty operator system prompt,
including whitespace, remains authoritative. Model, images, sampling, seed,
constraints, release policy and request deadlines remain caller-owned.

The existing ADV++ templates are useful comparison context, but are not copied
as candidates. Prompt Enhancer encourages added detail, photography terms and
quality tags, while Image2Prompt requires confident wording and extensive
physical/camera detail. Those instructions can conflict with a constrained
brief or insufficient visual evidence. This experiment tests narrower profiles
against a strong, fully instructed Freeform baseline rather than comparing a
rich candidate task against a bare phrase such as "output:".

## Frozen fixtures and candidate interventions

Source directory: `tests/benchmarks/task_profiles/`.

- `cases.json`: six fixed tasks, scoring facts, exclusions, seeds and settings.
- `profiles.json`: two evaluation-only portable profile snapshots.
- `bakeoff.py`: deterministic images, paired native Comfy API graphs, live
  execution recorder, blinded scorecards and summary calculation.
- `test_bakeoff.py`: offline controls, pairing, reproducibility and scorer gates.

The generated `plan.json` is the frozen execution artifact. Preserve its hash
before running; do not rewrite prompts, profiles, expected facts or decision
thresholds after seeing output. A revision creates a new experiment and a new
directory, with the original results retained.
Every execution and scoring command enforces the recorded harness SHA-256,
including its transport logic, scoring rules and decision thresholds.

| Arm | Intervention |
| --- | --- |
| Freeform | Exact shipped no-op `FREEFORM_PROFILE`; receives the complete task instructions below. |
| Prompt Fidelity | For text tasks only; generic fact/exclusion checking and wording improvement without invented scene/camera detail. |
| Visible Evidence | For image tasks only; generic counting, position, overlap, text and uncertainty checks. |

Candidates contain no task-specific expected answers. The profile's system
instruction applies only on cases with an empty operator system prompt. Two
cases deliberately supply a nonempty system prompt. Neither candidate can set
model, temperature, seed, image count or a release option. The executable
validator compares every paired Generate input and image hash; only the saved
profile node differs.

| Case | Complete task and grounded evidence | Main failure exposed |
| --- | --- | --- |
| text-object-fidelity | Rewrite the specified blue ceramic mug, white handle, yellow matte table, left window light and gray background, retaining all facts and exclusions. | Extra objects/material details, missing colors or placement. |
| text-medium-exclusions | Refine the specified black fox screen print on cream rough paper while retaining direction and banned effects. | Turning an illustration into photography, adding gradients, light or camera terms. |
| text-operator-system | Rewrite the specified overhead watercolor rowboat scene, with the operator's exact two-line format. | Overriding the nonempty system prompt or inventing scene content. |
| vision-count-relations | Describe one red circle, two blue squares, one lower green triangle and their positions. | Incorrect count, color or relation. |
| vision-readable-text | Transcribe the visible `OPEN 24` sign and locate the orange circle and blue square. | Altered OCR, invented place or an inferred 24-hour operating schedule. |
| vision-occlusion-unknown | Describe a brown rectangle covering part of a blue rounded shape; state that contents are not visible. | Invented material, object identity, hidden contents or hidden-object count. |

Image fixtures are deliberately simple diagnostic drawings, not photo-captioning
coverage. They use 640 by 480 integer geometry and hand-specified bitmap glyphs
through the already-declared Pillow dependency. No image model, font download,
external picture or extra dependency is involved. Generated PNG hashes and raw
RGB hashes are recorded. Inspect the three PNGs before the live run; expected
facts refer to visible pixels, not hidden drawing instructions.

## Run protocol and reproducibility

1. Record the exact installed plugin/Comfy/frontend revisions, llama.cpp build,
   model and projector SHA-256 values, GPU/driver and launch context size.
   Retain the effective model/projector labels and all relevant launch options.
2. Start one owned direct Qwen3-VL 4B Q5 runtime, with the resolved Q8 projector.
   Use the same resident process throughout the batch. The harness verifies the
   model/alias, runtime epoch and projector before/after every request.
3. Prepare a fresh artifact directory using the harness README. Inspect images
   and freeze the plan hash before collecting outputs. Expected facts remain
   outside the submitted graphs.
4. Run one text and one image Freeform warmup, excluded from all reported scores
   and latency ratios. Then run six cases at seeds 17 and 29, Freeform and its
   matching candidate for each pair: 24 measured generations, 26 total.
5. Counterbalance AB/BA order by case and seed. Keep the queue idle and run
   sequentially. Do not tune a sampler per profile or change a model mid-batch.
   Retain failed runs; reruns use a new directory.
6. Produce blinded scorecards. Score only against the shared task, rubric and
   actual fixture image. Finalize scores before revealing arm labels. If the
   scorer has already seen labels, report that limitation explicitly.
7. Summarize paired quality and latency, examine every constraint violation and
   unsupported claim, then record a ship/defer recommendation. Production
   profiles remain unchanged during the experiment.

Fixed Generate controls: Custom sampling; temperature 0.2, top P 0.9, top K 40,
min P 0.05, repetition penalty 1.0, presence/frequency penalties 0; Thinking Off;
256 output tokens; 120-second request deadline; `cache_prompt=false`;
`release_after_generation=false`; strict `raise_error` partial policy; no token
ban or structured-output constraint. Each pair uses identical images and seed.
An operator system override is identical in both arms. All controls are in the
plan and submitted native Comfy graphs.

Native history supplies response text and Comfy execution timestamps. Record
submission-to-history wall time too. Use Comfy execution time for ratios only
when it exists for every measured run; otherwise use wall time throughout.
This measures resident-model task latency including host overhead, not pure
token generation or model-loading time. No upstream token-rate claim is made.
Capture failures and timeouts without turning partial text into a successful
sample. The runner stops on the first failure, preserving its prompt ID and
history when available, and does not issue an unrelated/global interrupt.

## Scoring rubric fixed before output collection

Each measured response receives a score from 0 to 10:

`max(0, 4 * mean(fact scores) + 4 * mean(constraint passes) + clarity - hallucination penalty)`

- **Facts, maximum 4:** score every listed fact 1 when correctly and fully
  retained/described, 0.5 when recognizable but incomplete, and 0 when missing,
  contradicted or wrong. A synonym can earn full credit. For example, "off-white"
  and "cream" are equivalent here; "two blue squares" cannot become "blue shapes"
  without losing specificity. Cite the response span or state what is missing.
- **Constraints, maximum 4:** each explicit case constraint is pass/fail. Check
  word limits, paragraph/two-line format, forbidden additions and medium or
  visible-evidence limits. A failed automatic word/line check cannot be marked
  passing; passing automatic checks do not prove the remaining semantic rules.
  Word counts use whitespace-separated tokens. In the operator-format case,
  the 40-word limit applies only to its second line.
- **Clarity, maximum 2:** 2 means directly usable, coherent and free of redundant
  or distracting phrasing; 1 means understandable but requires minor editing;
  0 means unusable, contradictory or largely irrelevant. Longer output earns no
  intrinsic benefit. Provide a short reason.
- **Unsupported additions, penalty up to 4:** subtract 2 per distinct unsupported
  asserted detail, capped at 4. Quote each claim and explain the absent evidence.
  A wrong listed fact already loses fact credit; do not also list that same error
  as an added claim. A genuinely invented detail can also violate an explicit
  exclusion. Hedging does not excuse an invented detail when the task forbids
  inference. Ordinary visual shape labels supported by pixels are allowed.
- **Failure:** failed, timed-out or nonterminal generation scores 0 and cannot
  qualify a candidate for follow-up. A stopped/incomplete batch must be reported
  as incomplete, not summarized as if missing pairs passed.

All criteria require explicit evidence and a reviewed flag. Record "no
unsupported claims" as an empty reviewed list. The scorer checks geometry/OCR
against the PNG, not keyword presence alone. Synthetic unit-test scores prove
the aggregator's behavior and are never quality evidence.

## Decision gate

Evaluate text and vision candidates separately, using six paired observations
per family. A candidate is eligible for a separately scoped follow-up only if:

1. Mean paired quality gain is at least 1.0 point out of 10.
2. No case has a negative mean delta across its two seeds, and at least two of
   the three cases improve.
3. All candidate outputs complete, every explicit candidate constraint passes,
   and no candidate response contains an unsupported added claim.
4. Median paired resident-task latency is at most 1.25 times Freeform latency.

These are preselected product thresholds, not statistical significance tests.
If Freeform is already near the ceiling, no observed improvement means defer;
do not weaken its prompts to manufacture a gain. If only one narrow case helps,
park that observation for a targeted follow-up rather than installing a broad
profile. Even a pass supports only this model/quant/runtime and diagnostic task
set. Broader natural-image, multilingual and model-family coverage remains a
separate question. Any shipping change requires demonstrated benefit and a new
scoped Linear issue; this benchmark does not modify bundled profiles.

## Results and decision

All 24 measured requests and both excluded warmups completed on the same owned
Windows runtime. Before/after records retain the same model, projector and runtime
epoch throughout. Both seeds, all six cases and every planned pair are present.
The frozen rubric and decision thresholds above were used without post-result
changes.

**Defer Prompt Fidelity.** Its mean paired score decreased by 1.0 point, with
regressions on two cases and no gain on the third. **Visible Evidence is eligible
for follow-up, not shipping.** Its mean paired score increased by 2.0 points and
it passed the predeclared local follow-up gate. This result is restricted to the
recorded model/projector and these three diagnostic drawings.

| Candidate | Freeform mean / 10 | Candidate mean / 10 | Mean paired delta | Median paired latency ratio | Decision |
| --- | ---: | ---: | ---: | ---: | --- |
| Prompt Fidelity | 9.56 | 8.56 | -1.00 | 0.993 | Defer |
| Visible Evidence | 7.00 | 9.00 | +2.00 | 0.958 | Eligible for follow-up |

Quality values below show seed 17 / seed 29 in that order. These are composite
rubric scores, not task success rates or OCR accuracy.

| Case | Freeform scores | Candidate scores | Mean paired delta |
| --- | --- | --- | ---: |
| Object fidelity | 10 / 10 | 8 / 8 | -2.00 |
| Medium and exclusions | 10 / 10 | 9 / 9 | -1.00 |
| Operator system and exact format | 8.6667 / 8.6667 | 8.6667 / 8.6667 | 0.00 |
| Shape counts and relations | 10 / 9 | 10 / 9.5 | +0.25 |
| Readable text and geometry | 6 / 6 | 9 / 8.5 | +2.75 |
| Occlusion and unknown contents | 5.5 / 5.5 | 9 / 8 | +3.00 |

**All four sign transcriptions were wrong.** The PNG reads `OPEN 24`. Freeform
returned `BEWARE` at both seeds; Visible Evidence returned `BEWARE` and `E4`.
Every response therefore received zero credit for the transcription fact.
The candidate's higher composite score on this case reflects fewer unsupported
spatial claims and less redundant phrasing, not correct reading of the sign.
The other color, shape and position criteria still earn points under this rubric;
a score of 9 on this case must not be read as an OCR pass.

The text candidate added `casting gentle shadows` in both object-fidelity
responses, an unsupported lighting detail under the stated brief. Both
medium/exclusion responses dropped the explicit prohibition on camera terms,
although neither actually added camera language. The rewrite task required
preserving that exclusion. All four operator-format responses, across both arms,
produced two trailing spaces after the first-line `PROMPT:` and failed the exact
line-format requirement. These are observable output failures, not evidence that
the profile code replaced the operator's system instruction.

In the vision occlusion case, both Freeform responses reversed the covering
relationship and additionally claimed that only the rectangle's outer edge was
visible, despite its broad brown face. Both candidate responses identified the
rectangle as covering the circle, while retaining some incompleteness in size or
position descriptions. Neither arm invented rectangle contents. Freeform's two
sign responses also asserted exact symmetric placement that the drawing does not
show. The blinded reviewer treated that as a separate unsupported addition,
distinct from the wrong listed OCR fact.

| Family and arm | Responses with a failed constraint | Responses with unsupported additions |
| --- | ---: | ---: |
| Text Freeform | 2 / 6 | 0 / 6 |
| Prompt Fidelity | 4 / 6 | 2 / 6 |
| Vision Freeform | 0 / 6 | 4 / 6 |
| Visible Evidence | 0 / 6 | 0 / 6 |

All measured runs recorded Comfy execution timing, so the selected latency metric
is `execution_ms` throughout. Ratios are candidate/Freeform for each matched
case/seed, then summarized by their median. They are not ratios of pooled medians.
For reference, median resident execution times were 352.5 / 344.5 ms for text
Freeform/candidate and 463.0 / 448.5 ms for vision Freeform/candidate. Submission
wall times are preserved separately in the public artifact. These small samples
do not establish a statistically reliable speed improvement, and the timings
exclude model loading and the two warmups.

### Recorded runtime and frozen protocol

The executed model was `Qwen_Qwen3-VL-4B-Instruct-Q5_K_M.gguf`, with
`Qwen3-VL-4B-Instruct-abliterated-v1-mmproj-Q8_0.gguf`. These exact files define the
tested combination; no compatibility or quality claim extends to other exports.
Runtime discovery reports the projector from launch configuration, with its
separate compatibility state recorded as `unknown`.

| Recorded component | Value |
| --- | --- |
| Plugin revision | `f84e227878208b094ec8b0f9510f4d581e9960c6` |
| Comfy revision | `8d534945` |
| Frontend | `1.53.6` |
| Python / Torch | `3.13.7` / `2.9.1+cu130` |
| GPU / driver | NVIDIA GeForce RTX 5090 / `610.88` |
| llama.cpp | `b9957 c4ae9a88f` |
| Context / batch / GPU layers | `4096` / `512` / `999`, main GPU `0` |

Host-specific paths are replaced with placeholders in the public launch argv;
the effective settings and full model/projector hashes are retained.

| SHA-256 identity | Value |
| --- | --- |
| Frozen plan identifier | `372fbdc0e69c33cededa29e776f1ab04401744372aad616a19ed128f49b5d887` |
| Execution/scoring harness | `660a4776332d7925d5c5ee96497d2fcdc9eaf3b0cddd80d1e35745190b556e57` |
| Model | `026ad07482ca4a1121349fbb7b77abf9b81c0cbc156e3cf52ee7bb8d0229cde4` |
| Projector | `33d19545c921a784354b7cc099fa1f0e5b48352b73ab82bf077600e5ff9c6834` |
| Windows llama-server executable | `20b7b426afaa175e3374e16f2e99b2ecb1d63a2784c4a45e5c58141b0e6ff6ab` |

The public artifact also records the original source-file byte hashes. The frozen
plan identifier above is the harness's recorded plan identity, not the byte hash
of the sanitized export. Candidate profile snapshots, hashes, exact effective
prompts, system instructions, settings, seeds and execution order are included.

### Blinded scoring and public evidence

Two independent agent reviewers scored separate text and vision card sets with
arm labels withheld. The vision reviewer inspected all three actual PNGs. The
parent reviewed both sets before arm reveal. During that review, one proposed
vision penalty was removed: the rectangle starts left of the circle's center, so
the statement that the circle's left side is *partially* covered is supportable.
The associated missing-size/position deductions remained. This correction and
all scores were finalized before the arm mapping was used for aggregation.

| Finalized blinded scorecard file | SHA-256 |
| --- | --- |
| Text | `9d5a0aaa7b773726f57a7f49985803449573d94a2dbb34e4d7c85016c7bc02fe` |
| Vision | `b36c66fdd1c2ea5af09456a020e23c13b34073d74fdd561aefa3b539128f1b74` |

The [public result artifact](assets/2026-10-02-backlog/task-profiles/results.json)
contains all 26 responses, the 24 scored assessments with evidence, arm/seed/case
mapping, per-run latency, the frozen protocol, profile snapshots, scoring
provenance and exact summary. Warmups are labeled and have no assessment. The
[standalone rubric](assets/2026-10-02-backlog/task-profiles/rubric.md) and three
fixtures are published with their original PNG bytes and recorded RGB hashes:

| Fixture | PNG SHA-256 |
| --- | --- |
| [Counts and relations](assets/2026-10-02-backlog/task-profiles/count-relations.png) | `5bf93ec6cdeb0cbb4354c3da2725d89d2a50fb028633f9a0f0e9bb994a036830` |
| [Readable text](assets/2026-10-02-backlog/task-profiles/readable-text.png) | `3e8c04e8499332fa20c3d2e2e2eee6954a02b1c1c11977f791c59314a37e1f23` |
| [Occlusion](assets/2026-10-02-backlog/task-profiles/occlusion-unknown.png) | `9a693074799bfd999218406a809f1aea5f6c8e4d68159c2eb4041e5708b94767` |

The public export omits host usernames, private absolute paths, authentication
values, and native history/discovery envelopes. It preserves response whitespace
and scoring evidence exactly; the operator-format failure remains inspectable.
Private source artifacts were not rewritten during export. Export verification
recomputed all scores and paired latency ratios, matched the finalized summary,
checked all PNG/RGB hashes, and scanned decoded public strings for private paths
and authentication fields.

### Limits and next decision

This is six paired observations per candidate on one model/quantization/projector
and one runtime. The vision cases are three simple synthetic drawings; they do
not establish natural-image, multilingual, OCR, or cross-model robustness. The
literal format/exclusion requirements and subjective completeness, clarity and
unsupported-detail judgments are visible in every scorecard. There was one
reviewer per family plus parent review, not an inter-rater agreement study or a
statistical significance test.

[ENG-95](https://linear.app/7dev/issue/ENG-95/validate-the-visible-evidence-profile-on-held-out-images-before)
owns a bounded validation of the unchanged Visible Evidence candidate on held-out
images and two models before any shipping decision. Preserve the OCR failure as
a specific limitation to test, rather than treating its higher composite score as
resolved text recognition. Prompt Fidelity remains deferred on the current
evidence. No bundled profile, default, model setting, or public node behavior was
changed by this experiment.
