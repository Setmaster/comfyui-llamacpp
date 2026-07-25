# Active Auto VLM Projector Resolution Plan

Status: In progress
Date: 2026-07-24
Branch: `dev` (tracking `origin/dev`)
Baseline: `60ae2bfa686298edb80cad8280f1b0092af811c0`
Preserved release: tag `0.3.0` at `365986af4a47426b5513b3cee917ebec93a4204a`
Change bundle: `changes/2026-07-24-auto-vlm-projector-resolution/`

## Objective

Make the default direct-server `Vision Projector` setting perform safe, local,
automatic projector resolution while preserving exact explicit selection and
adding an explicit text-only mode. Diagnose unsupported image requests before
generation, keep runtime ownership and release behavior intact, and validate the
result through real Windows ComfyUI before handing `dev` back for user testing.

## Completion Contract

Outcome:

- One selector means exactly auto, text-only, or one concrete projector.
- Auto uses bounded GGUF metadata and selected tensor descriptors. It never
  downloads, guesses from adjacency alone, or allocates model/projector tensors.
- Confident matches launch with exact `--mmproj`; text-only launches use
  `--no-mmproj` and a projector-scrubbed child environment.
- Ambiguity and known missing-VLM projectors fail on Start before lifecycle
  mutation. Image capability failures are fixed, actionable, and non-leaking.
- Status, workflow migration, canonical example, and documentation describe the
  effective behavior.

Success checks:

- Gemma 3, Gemma 4, Qwen3-VL, Qwen3.5, and guarded MiniCPM metadata cases pass,
  including Qwen deepstack and mixed-size Gemma 4.
- Strong identity may resolve across configured roots. Same-width, weak, corrupt,
  escaped, or distinct candidates never auto-pair.
- Equivalent projector quantizations resolve deterministically to Q8_0, while a
  concrete selection remains exact.
- Released node positions and complete 0.2.1/0.3 contracts remain unchanged.
- Canonical Generate passive props blocks known-unsupported image requests
  before POST. Unknown attached/router capability remains compatible, and
  released legacy prompt nodes retain their contracts. Exact llama.cpp
  structured image errors map without retaining raw bodies.
- Existing process reuse/restart, native unload, explicit release, owned process,
  and RTX 5090 VRAM handoff behavior do not regress.
- Full automated, package, real Windows ComfyUI, independent review, remote
  `dev`, installed-clone, diff, and Project KB gates pass.

Stop condition:

- Stop only after the reviewed implementation is pushed to `origin/dev`, the
  maintained Windows clone matches the validated commit, no owned test runtime
  remains, and evidence is recorded, or at a genuine human/external gate.

Human gate:

- The user owns final hands-on blessing and any later merge or publication.

## Decision Log

- 2026-07-24: Supersede Work Block B's conservative "no automatic projector
  selection" decision because live acceptance proved that `(auto)` currently
  misrepresents behavior. Retain its original safety principle by requiring
  metadata-backed confidence and failing closed.
- 2026-07-24: Do not use llama.cpp `--mmproj-auto`; it controls Hugging Face
  downloads and does not scan local direct `-m` models.
- 2026-07-24: Use a dependency-free bounded GGUF reader. Do not add `gguf` to
  the Comfy environment.
- 2026-07-24: Allow catalog-wide fallback only for strong normalized name or
  base-lineage identity. Dimension or adjacency alone is insufficient.
- 2026-07-24: Group semantic quant variants and prefer Q8_0, then BF16, F16, and
  F32. This is a local resource policy, not a compatibility claim.
- 2026-07-25: Apply quantization preference across every strongly proven
  equivalent candidate, not only adjacent files. The installed Qwen3-VL 4B
  bakeoff therefore selects the lineage-linked global Q8_0 projector over its
  adjacent F16 copy.
- 2026-07-25: Cross-check modern projector metadata against the exact
  pinned-runtime output tensor interface before lifecycle mutation. Unknown
  families with a strongly plausible local projector require explicit
  selection instead of silently becoming text-only.
- 2026-07-25: Reject credential-bearing lineage repository URLs as identity
  evidence. They must never appear in errors, evidence, or browser-facing
  status.
- 2026-07-24: Keep router pairing unchanged.
- 2026-07-24: Preserve `master`, immutable 0.3.0, and unreleased 0.4.0 state.

## Execution Sequence

1. Implement and adversarially test the bounded GGUF reader.
2. Implement and corpus-test the typed resolver.
3. Wire direct config, child environment, status, and transactional manager
   behavior.
4. Add passive image capability and safe structured-error diagnostics.
5. Migrate the canonical workflow and update user documentation.
6. Run focused and full automated/package gates.
7. Run real Windows ComfyUI and GPU lifecycle gates.
8. Complete fresh-eyes review, fix findings, push `dev`, update the installed
   clone, and close the Project KB and native goal.

---

# Completed Canonical Generate Work Block B Plan

Status: Complete
Date: 2026-07-11
Branch: `dev` (tracking `origin/dev`)
Baseline: `7317660cdb0d6c17b4568ca2f80c5d2794699e61`
Preserved release: tag `0.3.0` at `365986af4a47426b5513b3cee917ebec93a4204a`
Change bundle: `changes/2026-07-11-canonical-generate-work-block-b/`

## Objective

Add one obvious, complete canonical llama.cpp generation surface for new
workflows while leaving all 17 released nodes and their saved-workflow contracts
unchanged. The new surface must remain local-first, use the existing external
llama-server runtime, provide bounded live feedback and truthful cancellation,
discover runtime facts without surprise model loading, preserve user-owned task
profiles as workflow snapshots, and make optional release-after-generation a
real terminal lifecycle promise.

## Completion Contract

Outcome:

- `LlamaCppGenerate` provides text, image, structured-output, token-ban,
  thinking, sampling, seed, partial-output, and release behavior through one
  compact V1 node.
- `LlamaCppTaskProfile` provides a bounded, update-safe user profile mechanism
  whose saved snapshot, rather than a mutable local name, drives execution.
- The existing typed `LLAMACPP_CONNECTION` remains the reusable connection
  object. Existing Start nodes remain byte-for-byte compatible and can connect
  their existing URL output directly to Generate.
- Generation returns response, thinking, and one typed, JSON-safe rich result.
- Fatal failures raise one actionable root cause and do not become valid output
  strings.
- Managed current llama.cpp builds receive true generation-scoped Stop through
  capability-probed resumable-stream control. Unsupported endpoints expose an
  explicitly whole-Comfy-job fallback.
- Passive discovery reports Known versus Unknown facts and never autoloads a
  model or silently replaces a missing saved selection.
- Direct release waits for every active direct lease. Router release waits only
  for the exact selected model and never falls back to stopping the router.

Success checks:

- The complete 0.2.1 and 0.3 fixtures retain every legacy class ID, schema,
  positional widget contract, output, default, and execution behavior.
- Default sampling omits expert sampler keys; Custom sends the complete group.
  Thinking Auto omits the upstream override; Off and On send explicit values.
- Text plus 0, 1, and 10 image cases, full Comfy batches, structured output, and
  token bans execute through the same payload and streaming path as legacy nodes.
- Live events are execution-scoped, reject stale events, emit at no more than
  8 Hz, retain no more than 64 KiB of valid UTF-8 per preview pane, and never
  serialize into workflows or API prompts.
- b9957-capable silent streams stop within two seconds, close the HTTP response,
  explicitly delete the upstream replay session on every exit path, and release
  their generation lease. Unsupported endpoints are never described as having
  node-local cancellation.
- Every passive properties request uses `autoload=false`; missing metadata stays
  Unknown; live router identities remain authoritative; projector candidates are
  suggestion-only and require confirmation.
- Profile storage is per Comfy user, bounded, GET-only, and resolved through
  Comfy's user manager. Freeform is the only built-in snapshot in this block.
- Concurrent direct and two-model router release races pass, including
  coalescing, admission blocking, timeout/cancelled waits, epoch changes, native
  `/free`, attached endpoints, and router failures without process fallback.
- Current ComfyUI classic and Nodes 2.0 pass discovery, profile, live preview,
  cancellation, seed, dynamic sampling/images, serialization, and App Mode
  checks.
- Real direct, VLM, structured, router, cancellation, native free, scoped
  release, driver-memory convergence, and downstream diffusion allocation checks
  pass on the current Windows RTX 5090 host.
- Python, JavaScript, lint, format, package, Registry validation, dependency
  audit, extracted-distribution tests, independent review, pushed `dev` CI, and
  exact installed-clone handoff are green.

Stop condition:

- Stop only when Work Block B is fully reviewed, committed, pushed to
  `origin/dev`, green in CI, installed at the exact validated commit, and recorded
  in the Project KB, or at a genuine human or external gate.

Human gate:

- The user owns hands-on acceptance and any later merge to `master`.

## Verification Closeout

- Exact executable candidate `e22518094af183e8f311dedda0739ebeb42f8526`
  passed the automated, browser, real-runtime, GPU-allocation, and independent
  review gates.
- Package-source candidate `af09ccb51b4e7a2b6ad351bb0b9d7cebebffaa97`
  differs only in packaged README metadata and passed the complete automated,
  extracted-package, Registry-validation, dependency-audit, and CI gates.
- GitHub Actions run `29195799658` passed five Linux jobs, one Windows job, and
  the quality job, including its package and frontend checks, at `e225180`.
- Package-source run `29196489323` passed the same seven jobs at `af09ccb`.
- The maintained Windows clone was clean at the same executable revision for
  the final live router and both-renderer gates. It is advanced to the final
  docs-only `dev` head during handoff without another runtime restart.
- The exact evidence, prompt IDs, artifact hashes, environment, accepted
  boundaries, and rollback state are recorded in
  [`docs/validation-0.4.md`](docs/validation-0.4.md).
- `master`, `origin/master`, and immutable tag `0.3.0` remained unchanged.
  Version 0.4.0 was validated but not published.

## Frozen Product Decisions

- Add exactly two public nodes: `LlamaCppGenerate` and
  `LlamaCppTaskProfile`. Do not add a public request-builder node.
- Preserve all legacy Start outputs. Generate accepts an optional typed
  Connection plus an optional advanced URL. Supplying both is an error; supplying
  neither targets the current managed runtime.
- Keep V1 for this additive surface. A V3 migration is not a prerequisite.
- Keep legacy `run_prompt()` and error-string compatibility unchanged. Canonical
  execution is a strict new facade over the same payload, stream, manager, and
  coordinator foundations.
- Use capability-probed llama.cpp resumable-stream control for exact Stop. Treat
  it as optional because upstream documents it as an internal interface.
- Keep native and explicit global release behavior unchanged. Add a private
  exact-target lease and release-handle path for canonical Generate.
- Reject release-after-generation on attached endpoints before sending a prompt.
  The lower-level global attached release remains a terminal no-op.
- No automatic projector selection and no capability inference from filenames.
- Profiles may transform prompt/system text only through a connected saved
  snapshot. They never override model, sampling, seed, release, or other
  generation controls.
- Ship only the no-op Freeform profile before the separate fixed bakeoff.

## Execution Sequence

1. Freeze typed request, result, profile, discovery, event, and release contracts
   with focused characterization tests.
2. Implement passive client/discovery facts and bounded per-user profile loading.
3. Implement runtime epochs, exact generation leases, scoped release operations,
   awaitable handles, and race tests while retaining global compatibility.
4. Implement stream progress, resumable control probing/cleanup, active execution
   registry, live events, and truthful cancel routes.
5. Implement canonical Generate and Task Profile nodes plus strict errors and
   frontend dynamic controls.
6. Add canonical workflows, App Mode surfaces, documentation, migration notes,
   and package coverage.
7. Run full automated, both-renderer browser, and real direct/router/GPU gates.
8. Complete independent full-diff review, resolve every P0/P1/P2 finding, push
   final `dev`, verify CI, install the exact head, and close the Project KB/goal.

## Explicit Non-goals

- Cloud-provider aggregation, agents, RAG, MCP, prompt databases, hidden or
  persistent chat sessions, model stores, downloads, or provider abstraction.
- Audio, transcription, video, explicit message arrays, prompt studio, diff,
  galleries, or result-unpacker nodes.
- Bundled target-aware profile content before a fixed bakeoff.
- Public arbitrary scoped-release APIs, automatic projector pairing, driver VRAM
  polling inside the coordinator, or a broad V3 migration.
- Any merge to `master`, mutation of `0.3.0`, or Registry publication during this
  work block.

---

# Completed UX Work Block A Plan

Status: Complete
Date: 2026-07-11
Branch: `dev` (tracking `origin/dev`)
Baseline: `40ff5d730dc27cb51d68ae53142cab9b4c91f5af`
Immutable release: tag `0.3.0` at `365986af4a47426b5513b3cee917ebec93a4204a`
Change bundle: `changes/2026-07-11-ux-work-block-a/`

## Objective

Make the existing 0.3 node surface substantially easier to discover, configure,
diagnose, and try without changing its released execution contracts. This work
implements only compatibility-safe UX Work Block A from the accepted post-refactor
Frontier Review. The canonical Generate and runtime redesign remains a later goal.

## Completion Contract

Outcome:

- A reviewable Work Block A implementation pushed only to `dev`.
- Friendly input labels, nested categories, useful search aliases, and advanced
  field disclosure across the existing 17-node V1 surface.
- Non-destructive template selection with frontend/backend parity and an explicit,
  undoable replace action.
- A visible, actionable setup and runtime diagnostic through the existing Server
  Status node.
- Canonical first-run workflow templates, bounded thumbnails, and a compact Start
  Here guide.

Success checks:

- Preserve all 0.2.1 and 0.3 node IDs, node display names, functions, output
  positions and types, input names and types, defaults, call ordering, and saved
  widget ordering.
- Freeze the full 0.3 schema before product edits and retain the existing 0.2.1
  compatibility fixtures unchanged.
- Advanced controls hide by default in both LiteGraph and Nodes 2.0, retain values,
  remain visible when linked, and do not shift the seed companion widget.
- Ordinary searches such as GGUF, VLM, prompt enhancer, JSON, and free VRAM find
  the intended nodes in the real frontend.
- Template selection fills exact-empty fields only, preserves whitespace and user
  drafts, treats Empty as a no-op, and permits an explicit replace that Ctrl+Z can
  undo.
- Idle Server Status reports a bounded binary, device, model-root, model, and
  projector setup summary without starting a server or claiming projector
  compatibility.
- The template browser lists each workflow once, thumbnails resolve, and Quick
  Text reaches real output on the current Windows ComfyUI and llama.cpp setup.
- Python, JavaScript, lint, format, package, Registry, saved-workflow, and real
  browser gates pass, followed by independent review and successful pushed `dev`
  CI.

Stop condition:

- Stop only after Work Block A is fully green, documented, committed, and pushed
  to `origin/dev`, with `master` and tag `0.3.0` unchanged.

Human gate:

- The user owns the later hands-on blessing and any merge to `master`.

## Execution Sequence

1. Freeze the complete 0.3 schema and write the implementation bundle.
2. Add presentation metadata and the renderer compatibility helper.
3. Align template behavior and add explicit undoable replacement.
4. Add bounded status/setup diagnostics.
5. Canonicalize and extend the first-run workflow surface.
6. Run automated, package, and Registry checks.
7. Validate both renderers and real generation in current ComfyUI.
8. Complete independent review, final diff review, push, CI, and KB closeout.

## Decisions

- Keep every released node-level display name. Friendly wording is additive input
  metadata, while nested categories and aliases improve discovery.
- Keep V1 for this retrofit. Current V1 accepts the required metadata, and a small
  instance-scoped frontend helper closes the classic renderer's advanced-widget
  mismatch without prototype-wide mutation.
- Extend Server Status rather than add an eighteenth node or a polling dashboard.
- Rename `examples/` to the canonical `example_workflows/` directory instead of
  creating duplicate template cards.
- Add only Setup Check and Quick Text in this block. Reuse the five existing
  engineering workflows and defer App Mode and richer task workflows.
- Keep provider aggregation, agents, RAG, MCP, prompt databases, persistent
  sessions, video, model stores, and canonical Generate work outside this goal.

## Verification Closeout

- Work Block A is implemented through reviewed candidate `4f1c585`, with the
  baseline `master`, `origin/master`, and immutable tag `0.3.0` unchanged.
- The final local candidate passed 431 Python tests plus 36 subtests, 27
  JavaScript tests, Ruff, format, JavaScript syntax, diff, Registry metadata,
  dependency audit, wheel, sdist, and Twine checks.
- The extracted source archive independently passed the complete Python and
  JavaScript suites. Distribution checks found nine workflow assets and 26
  source-test files, no duplicate members, and no tests in the runtime wheel.
- Real ComfyUI 0.27.0 with frontend 1.45.20 passed classic LiteGraph and Nodes
  2.0 disclosure, links, dynamic images, templates, search, setup diagnostics,
  all saved workflows, and exact 0.2.1 compatibility.
- Quick Text generated `QUICK_TEXT_BROWSER_OK` through llama.cpp b9957 on the RTX
  5090. Native Comfy free returned terminal completion, removed the owned
  Windows Job process, and returned the runtime to idle.
- A final compatibility retest proved historical 0.2.1 seeds remain fixed in
  real queue payloads and through Comfy serialize/reload under both renderers.
- Independent full-diff review found and closed one P1 and four P2 findings. The
  final verdict reports no remaining P0, P1, or P2 issue.
- GitHub Actions run `29177793143` passed all seven Linux, Windows, and quality
  jobs for pushed verification commit `f07564a`. The final docs-only closeout
  commit is required to pass the same matrix before handoff; its exact run and
  rebuilt artifact hashes are recorded in the Project KB.

Non-blocking boundaries:

- Browser/runtime evidence covers the current Windows host, not every platform
  and frontend version.
- Model catalog inspection is result-bounded but a very large or slow network
  root is not strictly time-bounded.
- The classic renderer adapter uses Comfy's legacy internal widget module and may
  need adaptation after a future frontend change.

---

# Completed SOTA Refactor Plan

Status: Complete
Date: 2026-07-10
Branch: `dev` (tracking `origin/dev`)
Change bundle: `changes/archive/2026-07-10-sota-refactor/`

## Objective

Turn `comfyui-llamacpp` into a current, dependable, focused llama-server integration for ComfyUI while preserving existing workflows and the external-process boundary that makes deterministic VRAM release possible.

## Completion Contract

Outcome:

- A reviewable implementation on the remote `dev` branch, with `master` untouched.
- Safe owned-process lifecycle on POSIX and Windows without global process-name killing.
- Correct current llama-server single-model and router contracts, including terminal unload barriers.
- ComfyUI native unload requests release this pack's owned llama-server resources, while all explicit stop and unload nodes remain.
- Compatibility-preserving prompt, VLM, structured-output, model, token, and lifecycle nodes.
- Automated unit, contract, process-tree, frontend, packaging, and compatibility tests.
- Current documentation, examples, versioning, CI, and a user-test handoff.

Success checks:

- Preserve every released node class ID, function, output tuple, socket name, default, and legacy positional widget prefix.
- Old workflow fixtures load without positional widget reinterpretation.
- All ten VLM image sockets are backend-visible and survive save/reload for counts 0, 1, and 10.
- Owned direct servers and router descendants are released with observable completion; unrelated same-name processes survive.
- Native Comfy `/free` and `/api/free` requests coalesce, defer through active generations, and release only owned resources.
- Router load and unload nodes wait for terminal state and never call download or cache-delete endpoints.
- External endpoints are never stopped by implicit lifecycle actions.
- Static checks, automated tests, package build, fresh Comfy import, live llama-server smoke tests, and available GPU handoff checks pass or are documented with exact remaining constraints.
- Final diff is reviewed, commits are pushed to `origin/dev`, and no merge to `master` is performed.

Stop condition:

- Stop only after the final completion gate passes and the remote `dev` branch contains the complete reviewed implementation, ready for the user's hands-on validation.

Human gate:

- The user owns final runtime blessing and any later merge to `master`.

## Execution Sequence

The detailed adaptive graph and wave checklist live in the change bundle. The intended sequence is compatibility characterization, shared foundations, lifecycle and protocol replacement, Comfy handoff integration, node and frontend consolidation, release polish, live validation, and independent final review. The sequence may change when evidence requires it, but the objective and compatibility contract do not.

## Decisions

- Keep V1 nodes as the canonical compatibility surface for this refactor. A future V3 migration can add replacements after the current V3 API stabilizes.
- Keep the external `llama-server` process boundary. Do not add in-process `llama-cpp-python`.
- Treat SOTA as the best focused llama-server integration, not an all-in-one agent, RAG, provider, or media suite.
- Preserve legacy startup defaults even where current llama-server defaults differ. New behavior is opt-in and capability-gated.
- Use one lifecycle coordinator for explicit nodes, native Comfy unload requests, shutdown, router residency, and active-generation leases.
- Never stop or unload an attached external endpoint through an implicit Comfy lifecycle action.

## Closeout

- Pre-handoff reviewed runtime revision:
  `9f83be46dbe2986600aa46d3890590931dffe26b`.
- Current post-deployment runtime revision:
  `e077006a46d842172c10a42d3ac8b95e848c4edd`.
- Pre-handoff cross-platform test and closeout revision:
  `fc3e25220cf53f5f86ff458afcdeb94659d35ca3`.
- Full evidence and known boundaries: `docs/validation-0.3.md`.
- Maintainer acceptance gate: `docs/user-acceptance.md`.
- Local verification: 384 tests and 36 subtests, 10 frontend tests, Ruff,
  formatting, JavaScript syntax, package build, fresh-wheel import, and current
  Comfy Registry validation passed.
- Native Windows verification: 340 tests and 36 subtests passed, with 44 POSIX
  or Linux-only tests skipped.
- The 150-case process suite passed 20 consecutive repetitions, for 3,000
  executions. Exhaustive and randomized streaming-redaction tests covered byte,
  UTF-8, line, size, credential, Authorization, and launch-generation
  boundaries. Warmed real process cycles returned to the exact descriptor
  baseline.
- GitHub Actions run `29148828684` passed all seven Linux, Windows, and quality
  jobs for the current runtime revision `e077006`. Pre-handoff run
  `29135583640` passed the same matrix for `fc3e252`.
- Real ComfyUI 0.27.0 and llama.cpp b9957 passed direct, router, VLM,
  structured-output, attached-endpoint, deferred-release, native-release,
  Windows Job, responsive-status, abrupt-owner, and RTX 5090 two-sided GPU
  handoff validation. A final direct/native-free smoke on `9f83be4` generated
  the exact requested text through an assigned Windows Job, then native free
  removed the PID and returned the runtime to idle without escalation.
- Independent lifecycle, compatibility, and release audits found and closed
  every P0/P1 issue. No known P0/P1 remains.
- Post-handoff runtime deployment pinned the active Windows RTX 5090 host to
  llama.cpp b9957 CUDA 13.3 and preserved b8261 as a complete versioned
  rollback. Live testing found and fixed SSE UTF-8 charset inference in
  `e077006`, then repeated direct, router, VLM, and native-release checks.
- `master` and `origin/master` remained at
  `1e3b7a5a2d90ee3a40cf40e01784de1c343ea85a`; no merge was performed.
- Registry publication remains outside this objective. The `setmaster`
  publisher endpoint returned 404 during validation, so publisher setup is a
  later external gate rather than a branch defect.

---

# ComfyUI llama.cpp Frontier Review Plan

Status: Complete
Date: 2026-07-10
Mode: Research and analysis only

## Objective

Produce a broad, deep, current, evidence-backed frontier review of this project. Establish what changed in ComfyUI, llama.cpp and llama-server, the surrounding ComfyUI LLM and prompt-enhancement ecosystem, and user expectations since this project was last updated. Convert that evidence into a ranked, bounded roadmap without implementing product changes.

## Completion Contract

Outcome:

- A durable Project Frontier Review at `docs/research/comfyui-llamacpp-project-frontier-review-2026-07-10.md`.
- Project KB updates that preserve the objective, research checkpoints, stable findings, and the final report pointer.

Success checks:

- Reconcile committed code, the existing dirty worktree, README, roadmap, packaging, and KB state.
- Research current ComfyUI architecture, custom-node APIs, frontend expectations, registry and packaging requirements, and relevant user workflow changes.
- Research current llama.cpp and llama-server command-line behavior, router support, HTTP endpoints, OpenAI compatibility, multimodal handling, structured outputs, tokenization, embeddings, monitoring, and lifecycle expectations.
- Compare meaningful alternatives for local and remote LLM use inside ComfyUI, including prompt enhancers, Ollama/API nodes, general LLM suites, VLM captioning, agentic workflows, and non-node alternatives where relevant.
- Include evidence from official documentation and repositories, active GitHub projects and issues, and Reddit/community discussion.
- Distinguish observed facts, source-backed inferences, recommendations, and uncertainty.
- Rank findings as P0/P1/P2/P3, with impact, recommended response, and proof surface.
- Separate next work, parked frontier, and things not worth copying.
- Include a proposed next milestone and acceptance gates.
- Review the final repo diff and verify that no product code was changed.

Evidence:

- Source URLs and retrieval dates in the report.
- Local git, code, packaging, and static-analysis evidence.
- GitHub repository metadata and release history where available.
- Reddit/community evidence treated as sentiment and workflow evidence, not authoritative technical documentation.

Stop condition:

- The report and KB are complete, internally cross-checked, and the active native goal passes its final completion gate.

Human gates:

- None for research. Any later implementation is a separate user-authorized work block.

## Milestones

1. Establish local baseline and stale/current boundaries. Complete.
2. Research ComfyUI platform evolution. Complete.
3. Research llama.cpp and llama-server evolution. Complete.
4. Map current ComfyUI LLM and prompt-enhancement alternatives. Complete.
5. Audit project gaps against the evidence. Complete.
6. Rank findings and challenge the proposed sequence. Complete.
7. Finalize report, KB, and verification. Complete.

## Decision Log

- 2026-07-10: Use the deep Project Frontier Review protocol because the request spans product, platform, runtime, ecosystem, and roadmap analysis.
- 2026-07-10: Preserve the existing 12-file dirty bundle as user-owned state and evaluate it without editing it.
- 2026-07-10: Prefer official sources for technical claims; use GitHub projects/issues and Reddit to measure alternatives, adoption patterns, pain points, and sentiment.
- 2026-07-10: Keep implementation out of scope. Only this plan, the research report, evidence artifacts, and Project KB may change.
- 2026-07-10: Rank the next implementation phase as compatibility stabilization and release readiness, not broad feature expansion or full V3 migration.
- 2026-07-10: Treat global llama-server process killing as a P1 release blocker rather than a P0 because it is severe when lifecycle nodes are invoked, but this review found no active outage or irreversible damage.
- 2026-07-10: Add a local-only end-user companion guide because alternatives occupy different product layers and the developer-oriented comparison was not sufficient for choosing a temporary tool.
- 2026-07-10: Recommend an external local runtime for generic interim use, with core `Generate Text`, Deno Local LLM Loader, Prompt Assistant, `comfyui-ollama`, or a specialist node selected according to the actual workflow need.
- 2026-07-10: Add a source-level LLM Party comparison because it is the one reviewed alternative that matches and exceeds the project's broad interaction outcomes while occupying a materially different runtime and application architecture.
- 2026-07-10: Treat LLM Party as an application-layer superset but not a llama.cpp integration superset. Preserve the focused external server, router, sampling, and dependency-isolation niche instead of competing on agents, RAG, media, social, and tool count.
- 2026-07-10: Refine the historical VRAM claim after a focused lifecycle audit. Current ComfyUI provides diffusion-style unloading for native `Generate Text` and custom models that explicitly use `ModelPatcher`, but arbitrary Transformers, in-process llama.cpp, and external runtimes remain outside automatic core ownership.
- 2026-07-10: Define the future product advantage as a confirmed two-sided VRAM handoff for arbitrary GGUF models, not merely an unload button. This requires safe owned-process tracking, Comfy-side eviction before LLM allocation, and process or provider-status confirmation before downstream GPU work.

## Closeout

- Final report: `docs/research/comfyui-llamacpp-project-frontier-review-2026-07-10.md`
- Local-only end-user companion: `docs/research/local-only-comfyui-llm-options-user-guide-2026-07-10.md`
- Deep LLM Party comparison: `docs/research/comfyui-llamacpp-vs-llm-party-deep-comparison-2026-07-10.md`
- Focused VRAM unloading audit: `docs/research/comfyui-local-llm-vram-unloading-audit-2026-07-10.md`
- Product code implementation: none in this review.
- Recommended next work: compatibility stabilization and release readiness.
- Final verification: all four research reports, plan, exact-answer log, and KB passed placeholder, conflict-marker, non-ASCII-dash, table-shape, and whitespace checks; LLM Party source and archive provenance hashes matched; both the deep comparison and focused VRAM audit passed independent fresh-eyes QA; `git diff --check` passed; and the tracked and untracked product-code file sets exactly matched the initial dirty baseline.
