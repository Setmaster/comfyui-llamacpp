# Fixed task-profile bakeoff

This developer benchmark compares the shipped Freeform snapshot with two
evaluation-only candidates. It does not install profiles or modify production
defaults. The protocol and eventual decision belong in
[`docs/research/task-profile-bakeoff-2026-10-02.md`](../../../docs/research/task-profile-bakeoff-2026-10-02.md).

Run from the repository with its existing development environment:

```bash
.venv/bin/python tests/benchmarks/task_profiles/bakeoff.py prepare /tmp/profile-bakeoff-live \
  --model 'exact/catalog-relative/model.gguf'
```

Preparation is offline. It creates three deterministic 640 by 480 PNGs and a
self-contained `plan.json`: six tasks, two fixed seeds, two arms per task, and
two excluded warmups. Expected facts and scoring criteria are stored separately
from the native Comfy API graphs. Both arms receive the same task prompt,
operator system prompt, images, model, seed, and generation settings. Only the
profile snapshot changes. The plan retains effective prompt/system text and
hashes for the plan, harness, profiles, paired inputs, PNGs, and raw RGB pixels.
Every later command verifies the executing harness hash against the plan, so
transport logic, scoring rules and decision thresholds stay fixed. A harness
edit requires preparing a new experiment directory.

For the live run, first start the intended owned direct model through the
normal Comfy workflow. Keep it resident, with the selected projector and fixed
launch settings. This harness does not start, stop, or reconfigure it. Prepare
an environment JSON file containing:

```json
{
  "repo_revision": "exact installed plugin commit",
  "model_sha256": "64 lowercase hex characters",
  "projector_sha256": "64 lowercase hex characters",
  "llama_build": "b9957, c4ae9a88f",
  "comfy_revision": "exact Comfy commit",
  "frontend_version": "installed version",
  "gpu": "model and driver",
  "context_size": 4096
}
```

Additional launch, binary-hash, software-version, and model-file labels are
welcome. The runner records this supplied evidence alongside observed passive
runtime identity. It checks exact model/alias selection, runtime epoch and
projector continuity; it does not independently compute remote weight hashes
or verify every launch setting in the supplied environment file.

```bash
.venv/bin/python tests/benchmarks/task_profiles/bakeoff.py run /tmp/profile-bakeoff-live \
  --server http://127.0.0.1:8188 --environment /tmp/profile-environment.json
.venv/bin/python tests/benchmarks/task_profiles/bakeoff.py scorecards /tmp/profile-bakeoff-live
```

`run` uploads only the generated images, requires an idle Comfy queue before each
request, and runs sequentially. It writes native API graphs, prompt IDs, complete
history, response text, runtime snapshots, and latency to `results.jsonl`.
Generation controls retain strict partial-output behavior, no release, and no
prefix-cache request. It stops on an error and preserves the failed prompt ID;
it does not issue a broad interrupt or retry a failed request. If the history
deadline expires, inspect that prompt before starting another run. Use a new
output directory for a rerun so prior evidence remains intact.

`scorecards.json` omits profile/arm labels and shuffles presentation. Give the
scorer that file, the generated images, and the protocol. Keep `scoring-key.json`,
`plan.json`, and raw results away from a blinded scorer until scores are final.
Fill every criterion and evidence field, clarity, and any unsupported claims;
then mark each card `reviewed: true`. Automatic word/line checks are supplements
to human or independent model review, not quality scores. The rubric requires
checking image facts against the actual PNGs.

`unsupported_claims` is a list of strings. Each string quotes the claim and gives
the reason it lacks support, for example `["\"a spoon\": no spoon is present"]`.
Use an empty list when no unsupported additions are found. Do not place objects
with separate claim/reason keys in this field.

```bash
.venv/bin/python tests/benchmarks/task_profiles/bakeoff.py summarize /tmp/profile-bakeoff-live
.venv/bin/python -m pytest -q tests/benchmarks/task_profiles
```

The summary rejects incomplete runs, altered responses, changed controls,
unreviewed scores, and stale scoring keys. Its result is `defer` or
`eligible_for_followup`, never an automatic profile installation. A positive
result still needs a separately scoped shipping issue and broader validation.

Latency is Comfy execution time when all history records provide valid timestamps;
otherwise it is observed submission-to-history wall time for every pair. This
includes host overhead and excludes initial model loading. Token throughput and
upstream generation-only timing are not available from native text history and
are not claimed. Two seeds do not establish statistical significance or
cross-model quality.
