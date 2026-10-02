# Comparable tools and the next feature frontier

Date: 2026-10-02
Baseline: `dev` at `c39e6a63b645a6de2e31bc3ba8cf16500967f296`
Tracking: [ENG-97](https://linear.app/7dev/issue/ENG-97/refresh-comparable-tool-research-and-define-the-next-useful-feature)

The useful gaps are **explicit messages, request-aware context budgets,
per-model router presets, a supported per-image caption pipeline, short-clip
transcription, and a profile editor**. I recommend keeping these as small additive
features over the existing execution/lifecycle code. First deliver the inexpensive
result/graph examples and prove native batch composition; then implement the
configuration and request features before the larger media/UI slices.

This refresh also rules out some apparent gaps: core Comfy already extracts JSON
fields, native list mapping already enables repeated calls, and advanced runtime
flags already pass through where they are not reserved. Broad agent/RAG tools,
persistent chat storage and a full prompt studio are not the next milestone.

## Purpose and evidence limits

Identify features supported by comparable tools that are missing or awkward in
this project, then decide which would improve real ComfyUI workflows. This is a
research report, not a claim that every competitor feature works reliably or an
authorization to install their runtimes.

The comparison uses the current **development candidate**, not just published
0.3.0. Generate, portable task profiles, automatic projector matching, live Stop,
and release-after-generation already exist on dev. The maintainer's hands-on and
release-promotion item, ENG-88, remains in Backlog.

Method:

- Read the current source and earlier July/October research before looking for
  gaps. Five independent review angles covered our baseline, prompt authoring,
  conversations/media, specialist workflows, and upstream runtime capabilities.
- Fetch existing relevant reference repositories and advance clean checkouts
  only with fast-forward merges. Clone new references under
  `~/Projects/References/github.com/`. Record old and inspected revisions.
- Use fresh primary-source web research to find additional close peers, then
  inspect their registered nodes and implementation paths. README statements
  alone are insufficient evidence for a high-priority recommendation.
- Do not execute competitor code, install dependencies, download models, or
  upgrade the maintained ComfyUI/llama.cpp installation. Source support and
  empirical runtime proof are identified separately.

The report's implementation suggestions are independent designs for our existing
runtime and generation layers. No competitor source was copied. Source reuse
would need a separate license review, especially for GPL-licensed peers.

## Current capabilities and decisions that remain valid

The current project already provides direct/router/attached connections,
metadata-backed projector matching, text and multi-image requests, JSON object,
JSON Schema and GBNF constraints, sampling and thinking controls, seed controls,
prompt-prefix caching, bounded streaming previews, exact cancellation where
supported, strict failure handling, a versioned result, and owned terminal
release. Those are not missing-feature findings.

Four distinctions matter when comparing it with peers:

1. **Full Batch is a joint request.** It sends multiple images in one user turn.
   It does not return one independently generated caption per frame. Comfy list
   mapping can compose repeated calls with other nodes; the missing convenience
   is a supported first-party batch contract and example.
2. **Prefix caching is not conversation history.** Payload assembly emits an
   optional system message and one user message. Concatenating earlier answers
   into a prompt is possible, but it loses explicit role boundaries.
3. **Structured generation is present; convenient consumption is incomplete.**
   Generate returns response/thinking strings and a rich typed result. This pack
   has no dedicated result accessor. Native Comfy JSON extraction already accepts
   the response STRING, and external consumers can inspect compatible result data.
4. **Audio compatibility has been demonstrated; a public audio feature has not.**
   The approved Qwen3-ASR trial verified one short synthetic English clip,
   streaming, cancellation, reuse and release on installed b9957. It did not
   establish general recognition quality or the Comfy AUDIO integration.

The failed Prompt Fidelity and Visible Evidence profile experiments remain
negative evidence against bundling those prompts. Competitors offering many
presets does not overturn those results. Freeform remains the only bundled
profile. Video, persistent sessions and a prompt studio are reconsidered below
as possible future work, not silently reactivated implementation.

Current-source anchors: [Generate schema](https://github.com/Setmaster/comfyui-llamacpp/blob/c39e6a63b645a6de2e31bc3ba8cf16500967f296/nodes/generate.py),
[payload assembly and structured completion checks](https://github.com/Setmaster/comfyui-llamacpp/blob/c39e6a63b645a6de2e31bc3ba8cf16500967f296/generation/execution.py),
[result contract](https://github.com/Setmaster/comfyui-llamacpp/blob/c39e6a63b645a6de2e31bc3ba8cf16500967f296/generation/contracts.py),
[audio evidence](llamacpp-audio-compatibility-2026-10-02.md), and
[held-out profile decision](visible-evidence-heldout-2026-10-02.md).

## Capability comparison

“Partial” includes capabilities already composable with standard Comfy nodes or
manual work. These are not all new inference features. Priorities below are
product recommendations, not release-blocking defect severities.

| User outcome | Comparable support | Ours today | Decision |
| --- | --- | --- | --- |
| Few-shot examples and multi-turn refinement with real roles | [LLM Party explicit history][party-history]; LLM Session also manages persistent sessions | One system/user exchange | Build explicit graph-owned messages; keep persistent storage separate |
| Check whether the actual request fits the model context | [llama.cpp request-body token counting][llama-count] | Text-only Token Count and context discovery are separate | Add capability-aware request budgeting |
| Different runtime settings for different router models | [Native llama.cpp INI presets][llama-presets]; llama-swap per-model config | Internal config field exists; managed Start node cannot select it | Expose presets with explicit inheritance |
| One caption per image with aligned results | [craftingmod sequential media node][peer-sequential], JoyCaption and [LLMNodes][llmnodes] | Composable through list mapping; Full Batch is one joint request | Prove a supported bounded workflow before adding an executor |
| Voice clip to text in the graph | [ThinkingLLM AUDIO node][gemma-audio], LLM Session, [Comfy core][core-textgen] | Positive compatibility spike, no public AUDIO adapter | Implement bounded experimental short-clip ASR |
| Create/edit/share reusable user instructions in the UI | [Deno user preset persistence][deno-presets], Prompt Manager | Profiles work, but authoring requires a JSON file | Add an editor over the existing snapshot contract |
| Extract structured text for downstream prompts | [Core Extract Text from JSON][core-json]; [specialist field outputs][specialist-fields] | Response STRING already compatible | Reuse core; add examples, not another parser |
| Inspect generation status/usage/release as graph data | [Task-specific typed field outputs][specialist-fields]; native generic utilities | Rich result exists, no first-party accessor | Small result inspector/serializer is useful support work |
| Edit and freeze an accepted prompt before expensive rendering | [Llama Prompt Generator editable versions][prompt-editor] | Full output/history exists; manual copy is possible | Bounded two-workflow recipe now; interactive adoption prototype later |
| Temporal video, regions/masks, document retrieval, autonomous tools | Specialist tools and broad suites | Frames/text/constraints cover some composition, not these full operations | Park or compose with existing specialists; do not expand indiscriminately |

## Worth implementing now

These are concrete candidates for this codebase, not recommendations to import
competitor dependencies. P2 denotes useful additive work; none is a demonstrated
P0/P1 release blocker. Relative effort describes integration surface, not a time
estimate.

### P2.1 - Explicit messages for few-shot and multi-turn workflows

**Observed:** [our payload builder][ours-payload] creates only an optional system
message and one user message. [LLM Party][party-history] exposes explicit history
input/output. Other peers use opaque session keys or disk history, which is a
separate product choice.

**Why:** a graph should be able to provide two example exchanges, ask for a rewrite,
then critique the answer without flattening every role into one prompt string.
This extends the existing generation surface without requiring a chat database.

**Bounded implementation:** add a versioned messages value with small builder and
append utilities, accepted by canonical Generate. Start with explicit
system/user/assistant text turns and the existing current-turn IMAGE inputs.
Define one unambiguous policy for mixing messages, prompt and task profile;
reject conflicting system sources rather than silently rewriting them. Preserve
role order, whitespace, byte/count limits, saved workflow portability and request
provenance. Keep tool execution, disk persistence, automatic summarization and
hidden conversation state outside this slice.

**Proof:** exact few-shot payloads, two-stage graph refinement, malformed roles and
oversized history, repeated queue runs without accumulated hidden turns, snapshot
round trips, images on the intended turn, cancellation and release through the
existing coordinator. Old workflow payloads must remain unchanged. Effort: medium.

### P2.2 - Request-aware context budgeting

**Observed:** [Token Count][ours-token] tokenizes supplied text, and failure returns
zero under its legacy contract. It cannot establish whether system text, the chat
template, images and requested output fit together. Current upstream documents
[counting a complete chat request][llama-count]. The installed b9957 source also
registers direct and router token-count routes, so this is not inherently an
upgrade requirement. [Pinned b9957 router wiring][b9957-count]

**Why:** users can choose a sensible context/output budget before an expensive
vision or long-history request fails. It also makes message chaining easier to
reason about.

**Bounded implementation:** reuse the final payload builder for an explicit budget
check and optional preflight. Report input count, authoritative context limit,
requested output allowance, remaining capacity, and the source of each fact.
Use the effective per-slot context, not a server-wide total. Treat input plus
requested output as an explicit budgeting policy, not a universal upstream
rejection rule: context shifting and generation limits can change that behavior.
Unknown capability is not a zero count. Start with known loaded direct targets;
probe exact direct/router behavior before expanding support. Counting may perform
real model/media work, so it must have leases, deadlines and cancellation and
must not be hidden inside passive browser discovery. Do not silently truncate,
summarize, reduce requested output, or autoload a model just to refresh a label.
Keep legacy Token Count behavior compatible; add a strict new path.

**Proof:** compare exact final request counts with generation usage for text,
nonempty system prompts, few-shot messages and one supported image model;
exercise fit/overflow, unavailable endpoint, unknown context, errors and a selected
router target without disturbing another model. Do not claim every multimodal
count is exact before those comparisons pass. Effort: medium.

### P2.3 - Per-model settings in the managed router

**Observed:** [RouterConfig][ours-config] accepts `models_preset`, but [the public
Start node][ours-router] does not provide it. The corresponding extra argument is
reserved. Moreover, emitted global context/GPU/batch settings take precedence
over [upstream per-model presets][llama-presets]. External servers and internal
Python callers can already use more of this capability.

**Why:** a small text model and a larger vision model often need different context,
GPU and projector settings. A managed router should preserve intentional
per-model choices instead of requiring one global compromise or external startup.

**Bounded implementation:** add a typed, local preset-file selection and explicit
“inherit preset” versus “override” behavior for overlapping settings. Preserve
legacy defaults and widget positions. Validate the file/option contract before
replacing a healthy runtime, expose effective provenance, and maintain exact
model identity and scoped unload semantics. Reuse llama.cpp's format instead of
creating a second model configuration language or downloader. Define how edits
to the same preset path take effect, through content identity or an explicit
reload contract. The initial feature uses local model files and must reject
implicit acquisition options such as remote model URLs or Hugging Face sources.

**Proof:** two local presets with different context/GPU settings; confirm effective
child parameters; prove explicit overrides win and inheritance omits competing
flags; same-path edits take effect under the documented reload contract; remote
acquisition settings cannot trigger downloads; invalid preset preserves the
running server; router discovery/generation/
scoped release and saved workflows remain compatible. Effort: medium.

### P2.4 - A supported per-image caption pipeline

**Observed:** [craftingmod's experimental sequential node][peer-sequential] runs
independent media requests and returns parallel output lists. Caption tools also
provide filename pairing and sidecars. Our Full Batch sends all frames in one
request, while [Comfy list mapping][core-map] can already invoke Generate itemwise.

**Why:** dataset captioning, image selection and asset inventories need one result
per image, with reliable alignment when a request fails or is cancelled. A single
paragraph describing twenty images is a different output.

**Bounded implementation:** first prove an example using existing list mapping and
one explicit final release. Only add a small orchestration node if the native
mapping cannot meet the required contract. Define ordered image IDs, prompt/seed
pairing, bounded item count, progress, strict versus explicitly marked partial
results, and one terminal release after the group. Comfy repeats the last value
of shorter lists, so mismatched non-scalar lists need explicit validation.
Preserve Full Batch's current meaning. Keep sidecar saving as a separate opt-in
step with explicit output location, overwrite policy, collision checks and
provenance-aware resume if offered. Never infer filenames from an anonymous IMAGE
tensor. Cleanup must run on failure and cancellation too; a downstream node that
only executes after success cannot alone establish that guarantee.

**Proof:** several distinct images produce the same number of correctly ordered
captions and result records; one shared prompt and exact-length prompt lists both
work; middle-item failure/cancellation cannot shift names or fabricate success;
the model stays loaded between items and releases once at the end or terminal
failure/cancellation; test downstream GPU allocation. Demonstrate composition before committing to a new executor.
Effort: small for an example, medium for aggregate coordination.

### P2.5 - Public experimental short-clip transcription

**Observed:** [ThinkingLLM][gemma-audio], LLM Session and core expose AUDIO inputs.
Our [completed audio trial](llamacpp-audio-compatibility-2026-10-02.md) already
proved bounded Qwen3-ASR compatibility on b9957. Repeating the same model download
or compatibility spike would add little information.

**Why:** a voice prompt can become transcript text and then enter the ordinary
prompt-generation/image workflow, with the runtime ownership users already have.

**Bounded implementation:** implement the report's separate transcription
operation: one mono PCM Comfy AUDIO item, at most ten seconds initially, converted
in memory to complete WAV bytes; an explicitly selected supported audio
model/projector; at most 128 output tokens; transcript plus preserved raw text and
language metadata. Strip only verified model-specific control prefixes. Reuse
canonical deadlines, cancellation and release. No microphone capture, automatic
download, timestamps, speech generation or claim of general audio understanding.

**Proof:** real Comfy AUDIO wiring on Windows, malformed/silent/oversized inputs,
missing capability, prefix variants, slow-peer deadline and cancellation,
release/reuse, and downstream text generation. The previous synthetic sentence
is compatibility evidence, not a broad accuracy benchmark. Effort: medium.

### P2.6 - UI authoring of user-owned task profiles

**Observed:** [our current controls][ours-profile] select, refresh and explicitly
copy a profile snapshot. [Deno][deno-presets] and prompt-management tools also let
users author saved instructions in the browser. We already have the portable
execution contract; the missing piece is authoring and inspection.

**Why:** users should be able to inspect a shared profile and edit its system,
prefix and suffix fields without locating a JSON file on disk.

**Bounded implementation:** add an optional editor/inspector and versioned
import/export over the existing profile schema. Separate saving the user's local
library entry from explicitly replacing a workflow snapshot. Keep Freeform
immutable, preserve whitespace, enforce existing bounds and current-user storage,
and detect conflicting saves using the previous content hash. No bundled prompt
library, sampler overrides or automatic snapshot updates.

**Proof:** create/edit/import/export/reopen a Unicode profile; invalid imports and
conflicting tabs preserve prior content; editing the library leaves existing
workflow behavior unchanged until explicit update; undo restores exact snapshot
bytes. Verify classic, Nodes 2.0 and applicable App Mode surfaces. Effort: medium.

### P3.1 - Small graph-usage improvements, largely by composition

Add a narrow inspector/JSON serializer for the existing [typed result][ours-result]
so usage, completion state, warnings and release evidence can be inspected without
custom Python. Keep the result schema authoritative; do not grow Generate into a
dozen output sockets. This is convenience over accessible data, not a missing
result contract.

Ship tested example recipes using [core JSON extraction][core-json] and
[Concatenate Text][core-concat]. The first turns an existing structured response
into a downstream prompt field. The second adds protected literal text after the
LLM, preserving names, weighting syntax and tags without asking a model to copy
them. Profile prefixes/suffixes modify the input sent to the model and do not
provide that guarantee. Core extraction returns empty text for missing values and
is not a schema validator; examples must explain that boundary.

A separate Generate/Review workflow followed by a render workflow with a saved
STRING also lets a user edit and freeze a prompt today. Document that queue
boundary explicitly: leaving an output-node Generate branch active can still
rerun it. A richer one-click adoption flow is parked below.

**Proof:** inspector output matches `GenerationResult.as_dict()` for complete and
marked-partial results; full response and release evidence survive serialization;
JSON-field and protected-literal examples work with installed core nodes; frozen
render runs make no new LLM request. Effort: small to medium. These examples reuse
existing mechanisms and make no new model-quality claim.

## Worth parking for later

- **Editable draft adoption and candidate comparison.** The Llama Prompt Generator
  implementation has editable output, versions and differences. A small Adopt
  action into a saved STRING is a sensible later prototype. It needs full-result
  retrieval, exact client/node/workflow identity, stale-result rejection, undo,
  and proof that rendering the accepted draft does not rerun Generate. A complete
  prompt studio or approval node paused inside a job is a larger scheduler/UI
  project. Start with the two-workflow recipe above.
- **Temporal video.** QwenVL, Simple Qwen and craftingmod expose video paths, but
  their transports differ. QwenVL passes sampled video frames to a Transformers processor;
  some GGUF paths instead sample still frames. Define timestamps/FPS, sampled-frame
  provenance, duration/pixel/context bounds and an exact supported server/model
  before promising motion understanding. Full Batch is already useful for joint
  still-frame questions. [QwenVL video paths][qwenvl-video]
- **Persistent sessions and KV snapshots.** LLM Session persists history and can
  summarize it; Ollama Chat uses a process-global session table. Neither is the
  same thing as a portable messages value. Add persistence only after explicit
  messages show a real need, with visible reset/storage/model-switch semantics.
  [LLM Session persistence][session-persistence], [Ollama session state][ollama-history]
- **Per-request reasoning budgets and “finish thinking.”** Upstream supports more
  than our Auto/On/Off controls. Startup reasoning-budget flags already pass
  through. A request-level budget may be useful before adding another live action,
  but requires model/template quality proof and exact stale-action protection.
  It is not equivalent to cancellation. [Upstream reasoning controls][reasoning]
- **Speculative decoding and cache diagnostics.** Startup flags already allow
  supported speculation and cache settings. A small benchmark/observability pass
  is more justified than another page of widgets. Speed, memory and quality
  effects need measurements on an actual target/draft combination; same seed does
  not guarantee the same sampled text. Do not create another persistent response
  cache before an explicit frozen-draft workflow establishes the need.
- **Rerank/embeddings and constrained decisions.** These are real upstream
  capabilities, but there is no established caption/prompt selection requirement
  here. A narrow rank-and-select operation would be more appropriate than a
  vector database if that requirement emerges. Finite-choice token likelihood is
  not calibrated confidence or verification of a visual fact.
- **Specialist region/OCR/tag recipes.** Florence2 returns real region/mask data;
  NeuralBooru has a separate tag validator that can already consume STRING output.
  Prefer optional crop/mask/tag composition examples before adopting their loaders,
  databases or runtimes. Accuracy and coordinate conventions need task-specific
  fixtures. Our failed OCR/profile results cannot be fixed by renaming an output
  “OCR” or constraining it to valid JSON. [Florence2 outputs][florence],
  [NeuralBooru validator][neuralbooru]

These parked items are recorded here and in the Project KB, without generating
an issue for every possible feature. A future product decision can select a
bounded item without reopening the completed research indiscriminately.

## Not worth copying

- **Broad agents, RAG, MCP, cloud-provider management or autonomous graph editing.**
  LLM Party and ComfyUI Assistant implement some of these behaviors, but importing
  that application scope would change what this pack is for. Our useful boundary
  remains a local external llama-server plus composable generation. The Assistant
  is a browser layer with [no generation-node registrations][assistant-nodes], and
  its widget edits do not universally wait for human approval.
  [Assistant implementation][assistant]
- **Another embedded inference stack or automatic model installer.** The current
  external process boundary already solves a deliberate dependency/VRAM ownership
  problem. The new craftingmod peer also has an external-server runtime session;
  that is parity, not an architectural feature we lack.
- **Generic JSON/regex/string nodes already supplied by Comfy.** Reuse native
  functions and document their limits. Parseable JSON is not independent schema
  validation, factual correctness, or a successful downstream workflow.
- **Untested prompt libraries or one node per target image model.** Model-specific
  prompt tools are worth studying, including the new Qwen Image PE reference,
  but their instruction catalog is not evidence of improvement on our models.
  The failed frozen profile gates stay in force.
- **Convenience that hides failure or state.** Do not copy silent caption skips,
  errors returned as usable text, implicit extra generation passes, history keyed
  only by a node ID, or caches keyed only by prompt/seed. A resumable sidecar step,
  if later included, must detect filename collisions and validate image/model/
  prompt/settings provenance before reuse.
- **A broad rewrite or a new protocol for its own sake.** Current extension APIs
  still support our integration. Source refresh alone does not justify migrating
  all nodes to V3, replacing the lifecycle coordinator, or adding a parallel
  Responses/tool-execution pipeline.

## Recommended implementation sequence

This order is an engineering recommendation based on current seams and proof
cost, not measured demand or a promise that all items should ship together.
The smallest earlier step that changes later design is to prove native graph
composition before creating new batch or parsing machinery.

1. **Graph interop and examples (P3.1).** Add the small result inspector and verify
   JSON-field, protected-literal and frozen-prompt recipes. Exercise a native
   mapped caption example to identify the minimum missing batch coordination.
2. **Router presets (P2.3).** Make per-model configuration accessible with explicit
   inheritance, retaining existing launch behavior by default.
3. **Explicit messages, then request budgeting (P2.1, P2.2).** Stabilize the final
   payload contract before sharing it with token preflight. Context checking is
   also useful for existing single-turn requests; messages are a sequencing
   preference, not a fundamental server dependency.
4. **Finish the caption pipeline (P2.4).** Implement only the orchestration that
   the native-list experiment proved necessary. Preserve ordered outcomes and
   batch-wide cleanup through failure as well as success.
5. **Profile editor (P2.6) and bounded ASR (P2.5).** These are independent additive
   slices. The editor does not depend on profile-quality research; ASR builds on
   the completed compatibility trial and needs its own native AUDIO acceptance.

The selected slices are in Linear Backlog with these completion contracts:

| Order | Selected slice | Linear |
| --- | --- | --- |
| 1 | P3.1: Expose generation results and document native graph composition | [ENG-99](https://linear.app/7dev/issue/ENG-99/expose-generation-results-and-document-native-graph-composition) |
| 2 | P2.3: Expose local router presets with explicit inheritance and reload semantics | [ENG-100](https://linear.app/7dev/issue/ENG-100/expose-local-router-presets-with-explicit-inheritance-and-reload) |
| 3 | P2.1: Add explicit graph-owned messages for few-shot and multi-turn generation | [ENG-101](https://linear.app/7dev/issue/ENG-101/add-explicit-graph-owned-messages-for-few-shot-and-multi-turn) |
| 4 | P2.2: Add request-aware token and context budgeting | [ENG-102](https://linear.app/7dev/issue/ENG-102/add-request-aware-token-and-context-budgeting) |
| 5 | P2.4: Support aligned per-image caption workflows with terminal cleanup | [ENG-103](https://linear.app/7dev/issue/ENG-103/support-aligned-per-image-caption-workflows-with-terminal-cleanup) |
| 6 | P2.6: Add user-owned task profile editing and import/export | [ENG-104](https://linear.app/7dev/issue/ENG-104/add-user-owned-task-profile-editing-and-importexport) |
| 6 | P2.5: Expose bounded experimental short-clip ASR through Comfy AUDIO | [ENG-105](https://linear.app/7dev/issue/ENG-105/expose-bounded-experimental-short-clip-asr-through-comfy-audio) |

This review does not start their implementation. ENG-88 remains the independent
maintainer/release-promotion backlog item; it does not prevent future development.

## Refreshed references

All **33** checkouts matched the live remote default-branch HEAD when independently
checked on 2026-10-02 at 16:13 UTC. Of these, **17 advanced**, **12 were already
current**, and **four were newly cloned**. Every working tree remained clean.
Commit dates retain their recorded timezone and are freshness evidence, not a
proxy for implementation quality. JamePeng's October 3 timestamp is UTC+08,
equivalent to October 2 UTC.

The machine-readable [reference manifest](assets/2026-10-02-feature-frontier/references.json)
records complete old/new SHAs, cache paths, branch identities and remote readback.
The old-named `craftingmod/ComfyUI-Ollama-ImageList` cache is preserved; GitHub
redirects it to `craftingmod/ComfyUI-llama-multimodal`. No duplicate clone was made.
Older July reports remain historical and were not rewritten.

| Reference | Previous | Inspected | Commit date | Refresh |
| --- | --- | --- | --- | --- |
| [1038lab/ComfyUI-JoyCaption](https://github.com/1038lab/ComfyUI-JoyCaption/tree/a0e9f0a17a5deb933fef341e2c7b0131e4f83c8a) | `a0e9f0a1` | `a0e9f0a1` | 2025-12-24 | unchanged |
| [1038lab/ComfyUI-QwenVL](https://github.com/1038lab/ComfyUI-QwenVL/tree/1b67b443918801f571714bab636edc1845b7002a) | `fcd1ada8` | `1b67b443` | 2026-09-14 | fast-forwarded |
| [abetlen/llama-cpp-python](https://github.com/abetlen/llama-cpp-python/tree/1652066e0af45f2313b339670ef9555e8a54e545) | `e894f0d6` | `1652066e` | 2026-09-30 | fast-forwarded |
| [ai-joe-git/comfyui_llama_swap](https://github.com/ai-joe-git/comfyui_llama_swap/tree/0ae61519ad61ec7d54558ec4344cc7fa15a714ca) | `0ae61519` | `0ae61519` | 2026-03-09 | unchanged |
| [BobbtheBuilder/ComfyUI_Assistant](https://github.com/BobbtheBuilder/ComfyUI_Assistant/tree/fd83e0a910e8759f8bf98f42f5260979c264eebc) | not cached | `fd83e0a9` | 2026-09-16 | cloned |
| [ChrisJohnson89/ComfyUI-NeuralBooru](https://github.com/ChrisJohnson89/ComfyUI-NeuralBooru/tree/e3376e5fcea10d38c52e8261f81f73fab6ef3352) | `e3376e5f` | `e3376e5f` | 2026-07-10 | unchanged |
| [Comfy-Org/ComfyUI](https://github.com/Comfy-Org/ComfyUI/tree/65787d668397d230bf5839d69a0a7239e2dad378) | `f3a36e74` | `65787d66` | 2026-10-01 | fast-forwarded |
| [Comfy-Org/ComfyUI_frontend](https://github.com/Comfy-Org/ComfyUI_frontend/tree/02364b5bbb23690e970213fc3e3457958bd0bda7) | `ceb5ae1e` | `02364b5b` | 2026-10-02 | fast-forwarded |
| [craftingmod/ComfyUI-llama-multimodal](https://github.com/craftingmod/ComfyUI-llama-multimodal/tree/127a8d3205dcdc854fd8ab2e65e9f6131357e337) | not cached | `127a8d32` | 2026-10-01 | cloned |
| [Deno2026/comfyui-deno-custom-nodes](https://github.com/Deno2026/comfyui-deno-custom-nodes/tree/bf906a47ae3c8353cce50a5098494006d7a33a4e) | `0ec785a6` | `bf906a47` | 2026-09-30 | fast-forwarded |
| [EricRollei/Local_LLM_Prompt_Enhancer](https://github.com/EricRollei/Local_LLM_Prompt_Enhancer/tree/0c07d6bdc7cd06db1fe32a82ebd731b37db2e66a) | `0c07d6bd` | `0c07d6bd` | 2025-12-19 | unchanged |
| [fpgaminer/joycaption_comfyui](https://github.com/fpgaminer/joycaption_comfyui/tree/8789c1cd683dcd08ab4c1ef7587fea4299025b44) | `8789c1cd` | `8789c1cd` | 2026-02-25 | unchanged |
| [FranckyB/ComfyUI-Prompt-Manager](https://github.com/FranckyB/ComfyUI-Prompt-Manager/tree/f9a973192490e7e7cb557bb9fcc305f9de4f386c) | `b7862153` | `f9a97319` | 2026-09-29 | fast-forwarded |
| [ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp/tree/4ebdf2c74acce30883d8e34b7c70b3eb8146f2fe) | `e3546c79` | `4ebdf2c7` | 2026-10-02 | fast-forwarded |
| [GlatTissekone/ComfyUI-Llama-Prompt-Generator](https://github.com/GlatTissekone/ComfyUI-Llama-Prompt-Generator/tree/5b7c2c35d58029debfa0c769fb3fc5fd93fecf12) | `5b7c2c35` | `5b7c2c35` | 2026-06-27 | unchanged |
| [goodguy1963/ComfyUI-ThinkingLLM](https://github.com/goodguy1963/ComfyUI-ThinkingLLM/tree/4729c7920053d5bf10d94d0f85cf050e243570f3) | `b3ee4397` | `4729c792` | 2026-09-27 | fast-forwarded |
| [heshengtao/comfyui_LLM_party](https://github.com/heshengtao/comfyui_LLM_party/tree/3790b26a438bb8e77c1ad24d93870f710cc8ae41) | `39bca5e4` | `3790b26a` | 2026-07-30 | fast-forwarded |
| [if-ai/ComfyUI-IF_AI_tools](https://github.com/if-ai/ComfyUI-IF_AI_tools/tree/93130d80ad90230bccc5c29f63f10a1c95d0eff9) | `93130d80` | `93130d80` | 2025-09-15 | unchanged |
| [if-ai/ComfyUI-IF_LLM](https://github.com/if-ai/ComfyUI-IF_LLM/tree/5967d4f742e323a934dc6b2ff0276998ea26e73b) | `5967d4f7` | `5967d4f7` | 2025-04-09 | unchanged |
| [JamePeng/llama-cpp-python](https://github.com/JamePeng/llama-cpp-python/tree/a90a018e7d03bd2a0c17a9889cfb3af5e75e53ee) | `7f59a861` | `a90a018e` | 2026-10-03 | fast-forwarded |
| [kantan-kanto/ComfyUI-LLM-Session](https://github.com/kantan-kanto/ComfyUI-LLM-Session/tree/a1cc725a40703eab1376e17fa2a1ff3757ee4c14) | `21ce9222` | `a1cc725a` | 2026-09-30 | fast-forwarded |
| [kantan-kanto/ComfyUI-MultiModal-Prompt-Nodes](https://github.com/kantan-kanto/ComfyUI-MultiModal-Prompt-Nodes/tree/b305642e711b84fefdf5d27db6860c086b1f3015) | `5ed1d44a` | `b305642e` | 2026-08-22 | fast-forwarded |
| [kijai/ComfyUI-Florence2](https://github.com/kijai/ComfyUI-Florence2/tree/9ece3de914214c5f581d725167bc9d0eeb0d1120) | `9ece3de9` | `9ece3de9` | 2026-05-06 | unchanged |
| [KLL535/ComfyUI_Simple_Qwen3-VL-gguf](https://github.com/KLL535/ComfyUI_Simple_Qwen3-VL-gguf/tree/52a3217e76e4702d097ae455b77bd82e04617acc) | `2a431790` | `52a3217e` | 2026-09-26 | fast-forwarded |
| [KohakuBlueleaf/z-tipo-extension](https://github.com/KohakuBlueleaf/z-tipo-extension/tree/6132862978021727284215bc2d5fe5257a872709) | `d6bbb867` | `61328629` | 2026-08-23 | fast-forwarded |
| [mostlygeek/llama-swap](https://github.com/mostlygeek/llama-swap/tree/42d8a5d33c370e9b769dc563a8009491342ef064) | `8945d2b7` | `42d8a5d3` | 2026-09-30 | fast-forwarded |
| [ollama/ollama](https://github.com/ollama/ollama/tree/b0c1ca4f7549d7acdfa52a7dcffc934bc63a43ce) | `82f905cd` | `b0c1ca4f` | 2026-10-01 | fast-forwarded |
| [orion4d/Orion4D_MetaPrompt](https://github.com/orion4d/Orion4D_MetaPrompt/tree/8eb2a1e8c88408ca527ed094a6892eed685e5a05) | `8eb2a1e8` | `8eb2a1e8` | 2026-07-04 | unchanged |
| [RealRebelAI/RebelsPromptEnhancer](https://github.com/RealRebelAI/RebelsPromptEnhancer/tree/f608f71d497e8f91fa93b66a5f3681c17a713b63) | `afbd625e` | `f608f71d` | 2026-09-21 | fast-forwarded |
| [stavsap/comfyui-ollama](https://github.com/stavsap/comfyui-ollama/tree/6db7560576e5a59488708e6be13e07b5aba2432a) | `6db75605` | `6db75605` | 2025-10-23 | unchanged |
| [William-Kruta/ComfyUI-LLMNodes](https://github.com/William-Kruta/ComfyUI-LLMNodes/tree/f30c0a7c62ce64f5b791796e095c710d53f5f713) | not cached | `f30c0a7c` | 2026-04-06 | cloned |
| [xiaowuapple-pixel/ComfyUI-Prompt-Enhancer](https://github.com/xiaowuapple-pixel/ComfyUI-Prompt-Enhancer/tree/9aedf78c8d18882caae1184e736719dffbc8c0cd) | not cached | `9aedf78c` | 2026-10-01 | cloned |
| [yawiii/ComfyUI-Prompt-Assistant](https://github.com/yawiii/ComfyUI-Prompt-Assistant/tree/e0587e6d50939ae05e92922f1ed2fce008c7f622) | `e0587e6d` | `e0587e6d` | 2026-04-25 | unchanged |

## Verification and limits

Reference refresh/readback commands used for every applicable checkout:

```bash
agent-ref ensure OWNER/REPO --update --json
git status --porcelain
git rev-parse HEAD
git -c core.hooksPath=/dev/null merge --ff-only origin/DEFAULT_BRANCH
git ls-remote --symref origin HEAD
```

Source inspection used scoped `rg`, `git show`, `nl -ba` and `sed`; exact b9957
source was read with `git show b9957:tools/server/server.cpp`. The report and
manifest are checked by a source-link/line-range and local-link validator,
JSON parsing, a full prose reread, independent review and `git diff --check`.
The [validation receipt](assets/2026-10-02-feature-frontier/validation.json)
records 33 references and 64 pinned source URLs checked without errors. The
[independent review receipt](assets/2026-10-02-feature-frontier/review.json)
records PASS with no remaining findings after two wording/acceptance refinements.
Product-source equality is checked with:

```bash
git diff --exit-code c39e6a6 -- runtime generation models nodes web tests
```

No local product test suite, competitor test suite, inference benchmark or browser
acceptance was run for this source-inspection phase. Normal branch CI runs after
the documentation push; it does not establish the proposed features' runtime
behavior. New endpoint/UI compatibility, generated
quality, throughput and resource use remain the future issues' explicit gates.
The prior completed candidate validations remain the evidence for current code.

Rollback: revert the research documentation commit. The reference manifest retains
previous SHAs for historical inspection with `git show` or a separate reference
worktree; do not reset a cache that another task has since changed. No maintained
runtime, model inventory, stable branch, release tag or publication was changed.

[assistant]: https://github.com/BobbtheBuilder/ComfyUI_Assistant/blob/fd83e0a910e8759f8bf98f42f5260979c264eebc/web/chatbot.js#L1092-L1108
[b9957-count]: https://github.com/ggml-org/llama.cpp/blob/c4ae9a88f8884ee5a155c8349ace9ea31a58007f/tools/server/server.cpp#L204-L208
[core-concat]: https://github.com/Comfy-Org/ComfyUI/blob/65787d668397d230bf5839d69a0a7239e2dad378/comfy_extras/nodes_string.py#L39-L59
[core-json]: https://github.com/Comfy-Org/ComfyUI/blob/65787d668397d230bf5839d69a0a7239e2dad378/comfy_extras/nodes_string.py#L410-L440
[core-map]: https://github.com/Comfy-Org/ComfyUI/blob/65787d668397d230bf5839d69a0a7239e2dad378/execution.py#L251-L327
[core-textgen]: https://github.com/Comfy-Org/ComfyUI/blob/65787d668397d230bf5839d69a0a7239e2dad378/comfy_extras/nodes_textgen.py#L27-L76
[deno-presets]: https://github.com/Deno2026/comfyui-deno-custom-nodes/blob/bf906a47ae3c8353cce50a5098494006d7a33a4e/web/js/deno_local_llm_refiner.js#L6725-L6786
[florence]: https://github.com/kijai/ComfyUI-Florence2/blob/9ece3de914214c5f581d725167bc9d0eeb0d1120/nodes.py#L309-L428
[gemma-audio]: https://github.com/goodguy1963/ComfyUI-ThinkingLLM/blob/4729c7920053d5bf10d94d0f85cf050e243570f3/AILab_QwenVL_GGUF.py#L2416-L2449
[llama-count]: https://github.com/ggml-org/llama.cpp/blob/4ebdf2c74acce30883d8e34b7c70b3eb8146f2fe/tools/server/README.md#L1567-L1594
[llama-presets]: https://github.com/ggml-org/llama.cpp/blob/4ebdf2c74acce30883d8e34b7c70b3eb8146f2fe/tools/server/README.md#L1872-L1931
[llmnodes]: https://github.com/William-Kruta/ComfyUI-LLMNodes/blob/f30c0a7c62ce64f5b791796e095c710d53f5f713/llama_cpp_nodes.py#L365-L507
[neuralbooru]: https://github.com/ChrisJohnson89/ComfyUI-NeuralBooru/blob/e3376e5fcea10d38c52e8261f81f73fab6ef3352/validator_node.py#L23-L136
[ollama-history]: https://github.com/stavsap/comfyui-ollama/blob/6db7560576e5a59488708e6be13e07b5aba2432a/CompfyuiOllama.py#L578-L650
[ours-config]: https://github.com/Setmaster/comfyui-llamacpp/blob/c39e6a63b645a6de2e31bc3ba8cf16500967f296/runtime/config.py#L308-L395
[ours-payload]: https://github.com/Setmaster/comfyui-llamacpp/blob/c39e6a63b645a6de2e31bc3ba8cf16500967f296/generation/execution.py#L531-L570
[ours-profile]: https://github.com/Setmaster/comfyui-llamacpp/blob/c39e6a63b645a6de2e31bc3ba8cf16500967f296/web/task_profile.js#L175-L203
[ours-result]: https://github.com/Setmaster/comfyui-llamacpp/blob/c39e6a63b645a6de2e31bc3ba8cf16500967f296/generation/contracts.py#L864-L1009
[ours-router]: https://github.com/Setmaster/comfyui-llamacpp/blob/c39e6a63b645a6de2e31bc3ba8cf16500967f296/nodes/start_router.py#L245-L309
[ours-token]: https://github.com/Setmaster/comfyui-llamacpp/blob/c39e6a63b645a6de2e31bc3ba8cf16500967f296/nodes/token_count.py#L125-L138
[party-history]: https://github.com/heshengtao/comfyui_LLM_party/blob/3790b26a438bb8e77c1ad24d93870f710cc8ae41/llm.py#L1500-L1518
[peer-sequential]: https://github.com/craftingmod/ComfyUI-llama-multimodal/blob/127a8d3205dcdc854fd8ab2e65e9f6131357e337/backend/nodes/llama_cpp_session.py#L737-L821
[prompt-editor]: https://github.com/GlatTissekone/ComfyUI-Llama-Prompt-Generator/blob/5b7c2c35d58029debfa0c769fb3fc5fd93fecf12/js/prompt_generator_pro.js#L3038-L3089
[qwenvl-video]: https://github.com/1038lab/ComfyUI-QwenVL/blob/1b67b443918801f571714bab636edc1845b7002a/py/AILab_QwenVL.py#L767-L800
[reasoning]: https://github.com/ggml-org/llama.cpp/blob/4ebdf2c74acce30883d8e34b7c70b3eb8146f2fe/tools/server/README.md#L1445-L1459
[session-persistence]: https://github.com/kantan-kanto/ComfyUI-LLM-Session/blob/a1cc725a40703eab1376e17fa2a1ff3757ee4c14/services/history_persistence_service.py#L92-L147
[specialist-fields]: https://github.com/xiaowuapple-pixel/ComfyUI-Prompt-Enhancer/blob/9aedf78c8d18882caae1184e736719dffbc8c0cd/qwen_image21_pe.py#L1563-L1570
[assistant-nodes]: https://github.com/BobbtheBuilder/ComfyUI_Assistant/blob/fd83e0a910e8759f8bf98f42f5260979c264eebc/__init__.py#L3-L19
