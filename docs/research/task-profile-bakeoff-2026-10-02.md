# Task-profile bakeoff, 2026-10-02

Status: protocol prepared; live runs and scoring pending.
Tracking: ENG-89. Runtime baseline: `a466b6b`.

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

Pending live execution and independent scoring. No candidate benefit,
latency result or ship recommendation is claimed yet.

| Evidence | Result |
| --- | --- |
| Installed runtime/environment | Pending |
| Frozen plan hash and artifact path | Pending |
| Measured completions | Pending (target 24/24) |
| Text mean paired quality delta | Pending |
| Vision mean paired quality delta | Pending |
| Text/vision median latency ratios | Pending |
| Constraint failures and unsupported claims | Pending |
| Scoring provenance and blinding | Pending |
| Follow-up/defer decision | Pending |
