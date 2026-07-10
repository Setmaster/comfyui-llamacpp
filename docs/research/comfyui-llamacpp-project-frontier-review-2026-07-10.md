# ComfyUI llama.cpp Project Frontier Review - 2026-07-10

Status: Final research report
Mode: Research and analysis only, no product implementation
Repository: `github.com/Setmaster/comfyui-llamacpp`

## Purpose

This review determines what changed around `comfyui-llamacpp` since its last committed update, what remains valuable, what has become stale or risky, what alternatives now exist, and which improvements should be prioritized next.

The user explicitly requested a broad and deep research task, online research including Reddit and GitHub, multiple agents, Project KB documentation, and no implementation yet. This report is therefore a decision artifact, not a patch plan already in execution.

## Method

Evidence sources:

- Local repository inspection, including committed code, README, packaging, web assets, and the existing dirty worktree.
- Project KB inspection and updates under `~/agent-prj-data/prj_data/github.com/Setmaster/comfyui-llamacpp/`.
- Independent read-only sub-agent review angles for architecture and ComfyUI/platform compatibility, ecosystem alternatives/community evidence, reliability/API drift, and final report QA.
- Official ComfyUI documentation, ComfyUI and frontend repositories, Comfy Registry API, and GitHub release/repository metadata.
- Official llama.cpp server documentation, current source/tests in a reference checkout, and GitHub metadata/compare ranges.
- GitHub and Registry scans for competing ComfyUI LLM, prompt-enhancement, captioning, and adjacent workflow projects.
- Reddit/community threads as workflow and sentiment evidence. These are not treated as authoritative technical documentation.

Important distinction:

- Observed facts are things directly visible in local code, official docs, current repositories, or command output.
- Inferences are the review's interpretation of those facts.
- Recommendations are proposed next work, not implemented changes.
- Uncertainty is called out where runtime behavior still needs a ComfyUI plus llama-server smoke test.
- Star, fork, download, release, and pushed-at counts are point-in-time metadata retrieved on 2026-07-10.

## Scope And Baseline

Local project baseline:

- Repo: `github.com/Setmaster/comfyui-llamacpp`
- Branch: `master`
- HEAD: `1e3b7a5a2d90ee3a40cf40e01784de1c343ea85a`
- Last committed change: 2026-03-10, `Fix prompt output text lost on tab switch`
- Package version: `0.2.1`
- GitHub metadata on 2026-07-10: 2 stars, 0 forks, 0 open issues, no detected license, last pushed 2026-03-10
- Comfy Registry lookup on 2026-07-10: `https://api.comfy.org/nodes/comfyui-llamacpp` returns 404

Existing dirty source state at review time:

- Modified tracked files: `model_manager.py`, `nodes/__init__.py`, `nodes/adv_prompt.py`, `nodes/advpp_prompt.py`, `nodes/basic_prompt.py`, `nodes/start_router.py`, `nodes/start_server.py`, `server_manager.py`
- Untracked product files: `nodes/model_info.py`, `nodes/server_utils.py`, `nodes/structured_output.py`, `nodes/token_count.py`
- Review artifacts created for this task: `PLANS.md`, `docs/research/comfyui-llamacpp-project-frontier-review-2026-07-10.md`

The dirty product bundle appears to add Model Info, Token Count, Structured Output, stop sequences, shared model/server helpers, parameter parity, and crash-read hardening. This review evaluates that bundle but does not commit, discard, or modify it.

## Current State

The committed project is a small ComfyUI custom node pack for calling `llama-server` through OpenAI-compatible HTTP endpoints. Its main value is that it keeps local GGUF LLM execution outside the ComfyUI Python process. That remains a useful architectural position because it avoids `llama-cpp-python` build issues and reduces dependency conflict with ComfyUI.

Core pieces:

- `server_manager.py` starts and stops `llama-server` in single-model and router modes.
- `model_manager.py` discovers GGUF files under a hardcoded `ComfyUI/models/LLM/gguf` path.
- `streaming_client.py` calls `/v1/chat/completions` with SSE streaming and extracts normal and reasoning content.
- `nodes/basic_prompt.py`, `nodes/adv_prompt.py`, and `nodes/advpp_prompt.py` expose prompt nodes.
- `web/adv_prompt.js` and `web/advpp_prompt.js` dynamically add image sockets based on `image_amount`.
- `nodes/prompt_output.py` and `web/prompt_output.js` display generated text on the canvas.

The main weakness is that the project has not kept up with ComfyUI frontend/API evolution, llama.cpp router/server API evolution, Registry expectations, and the now-crowded ComfyUI LLM/prompt ecosystem. The repo is still small enough to recover with a focused compatibility stabilization milestone.

## Executive Summary

No true P0 was identified. There is no evidence from this review that the repo is currently causing a production outage or corrupting data merely by existing.

The next implementation block should not be a feature expansion. It should be a compatibility and release-readiness block:

1. Make managed `llama-server` lifecycle safe.
2. Update router and endpoint contracts to current llama.cpp behavior.
3. Fix model discovery to respect ComfyUI model paths.
4. Verify V1 dynamic image inputs and saved workflow compatibility under current ComfyUI.
5. Clarify `cache_prompt` versus real chat history.
6. Add minimal contract tests and a runtime smoke matrix.
7. Package and document the node for Registry/Manager users.

The project should stay focused. The strongest product position is not "be LLM Party." It is:

> A lightweight, reliable ComfyUI client and optional lifecycle wrapper for external llama.cpp-compatible servers, with good model discovery, structured output, visible progress, prompt-fidelity workflows, and VRAM handoff.

That niche is still valid because the ecosystem has split into three rough groups:

- Large all-in-one LLM node suites that are powerful but heavy.
- Prompt-assistant and prompt-enhancer nodes that focus on UX and workflow generation.
- Thin local-server clients that users want because they avoid Python dependency pain and can free VRAM.

`comfyui-llamacpp` should compete in the third group and borrow only selected UX ideas from the second.

## What Changed In ComfyUI

### Release pace and baseline drift

The repo's last committed update was 2026-03-10. Current ComfyUI metadata on 2026-07-10 shows active upstream development the same day. The compare range from a ComfyUI commit near this repo's March baseline to current master is 687 commits ahead.

Observed evidence:

- Current ComfyUI GitHub metadata on 2026-07-10: 120,206 stars, 14,125 forks, GPL-3.0, pushed 2026-07-10.
- Recent releases include `v0.27.0` on 2026-06-30, `v0.26.0` on 2026-06-23, `v0.25.1` on 2026-06-18, `v0.25.0` on 2026-06-16, and multiple earlier releases after this repo's last update.
- Compare URL: `https://github.com/Comfy-Org/ComfyUI/compare/8086468d2a1a5a6ed70fea3391e7fb9248ebc7da...master`

Impact:

- Compatibility cannot be assumed from March behavior.
- Saved workflow serialization, frontend hooks, custom node APIs, desktop distributions, and registry expectations all need to be revalidated.

### V3 node migration exists, but V1 is still relevant

ComfyUI now documents a V3 custom node schema with `ComfyExtension`, async node list behavior, dynamic combos, autogrow widgets, async execution, progress reporting, `ui.PreviewText`, and node replacement helpers. The documentation also indicates future node features will primarily target V3.

However, V1 Python nodes still exist and are still documented for current custom nodes. A full V3 rewrite is not required before making this project useful again.

Recommendation:

- Stabilize current V1 nodes first.
- Use V3 ideas selectively where they solve immediate problems, especially output preview, dynamic inputs, widget serialization, progress, and future node replacement.
- Park a full V3 migration until after runtime compatibility is proven.

### Core ComfyUI now has first-party text generation surfaces

Current ComfyUI includes core text generation nodes and official partner API nodes that overlap with simple LLM use cases.

Observed examples:

- Core `TextGenerate` node in `comfy_extras/nodes_textgen.py`.
- Core `TextGenerateLTX2Prompt` prompt enhancer.
- Official partner nodes and docs for OpenAI Chat, Anthropic Claude, OpenRouter LLM, and ByteDance LLM.
- Recent releases added or expanded Qwen text generation and multimodal text generation support.

Impact:

- A basic "send prompt, receive text" node is no longer enough as a differentiator.
- The project has to be better at local llama.cpp server control, router/model lifecycle, GGUF discovery, structured outputs, and Comfy workflow ergonomics.

### Frontend and serialization behavior changed enough to matter

The current frontend reference checkout has package version `1.48.1` and recent commits from 2026-07-10. The local project uses older extension patterns in several places.

Observed local risks:

- `web/prompt_output.js` uses `showValueWidget.inputEl`, while current frontend code marks `inputEl` as deprecated in favor of the widget element abstraction.
- The prompt output display widget serializes a value to preserve it, but current frontend docs distinguish workflow serialization from API prompt serialization. The current output widget likely needs to be explicitly UI-only or replaced with a modern preview output pattern.
- `web/adv_prompt.js` uses `imageAmountWidget.value || 2`, so a saved `image_amount` of `0` reloads as `2`.

Impact:

- Saved workflows may drift.
- API prompt JSON may include UI-only display state.
- Dynamic sockets may not behave as expected under current frontend or Node2 behavior.

### Registry and Manager expectations matter more now

The Comfy Registry is now a primary discovery and install path for custom nodes. This project is not present in the Registry.

Observed evidence:

- `https://api.comfy.org/nodes/comfyui-llamacpp` returns `{"error":"","message":"Node not found"}` with HTTP 404.
- Registry docs describe package metadata, standards, and publishing flow.
- Registry standards expect nodes to be functional, documented, actively maintained, and not interfere with other nodes.

Impact:

- Users loading shared workflows may see missing node errors and have no Manager-backed install path.
- Current lifecycle behavior that kills all `llama-server` processes by name conflicts with the "not interfere with other nodes" expectation.

## What Changed In llama.cpp And llama-server

### Release pace and baseline drift

llama.cpp has moved substantially since both the README's minimum router build reference and this repo's March update.

Observed evidence on 2026-07-10:

- Current llama.cpp GitHub metadata: 119,902 stars, 20,414 forks, MIT, pushed 2026-07-10.
- Local reference checkout HEAD: `07d937828636e305bc0cfe738b288f9ab05ff748`.
- Compare from a commit near this repo's March baseline to master: 1,687 commits ahead.
- Compare from the README's referenced router build `b7389` to master: 2,576 commits ahead.

Impact:

- The project's llama-server assumptions must be treated as stale until proven against a current binary.

### Router model endpoints changed

Current llama-server docs describe router behavior that differs from this project's local lifecycle code.

Observed current docs:

- POST endpoints route by JSON body `"model"`.
- GET endpoints such as `/props` and `/metrics` route by `?model=`.
- `GET /models` lists model status and supports `?reload=1`.
- `POST /models/load` loads a model.
- `POST /models/unload` unloads a model.
- `POST /models` now downloads a model in current docs.
- `/models/sse` emits router loading, download, progress, and sleep events.

Observed local code:

- `server_manager.py` tries `POST /models` before `POST /models/load` in `load_model()`.
- Dirty `nodes/model_info.py` uses `/props`, but the review found no router `?model=` handling.
- Dirty `nodes/token_count.py` exposes `/tokenize` but has no explicit model identity input.

Impact:

- The current `POST /models` probe is no longer a harmless compatibility fallback. In current llama-server it can mean model download.
- Router mode tools may query the wrong loaded model or fail silently.

### Server CLI and default behavior evolved

Current server features include OpenAI-compatible chat/completions/responses/embeddings, Anthropic messages, rerank, continuous batching, multimodal image/audio/video, schema JSON, tool calling, speculative decoding, web UI, metrics, sleep idle, and router configuration through models-dir, presets, cache, and Hugging Face paths.

Observed drift:

- Current `--ctx-size` default is `0`, meaning loaded from model. The local UI defaults to `4096` and uses a minimum of `512`, which can unnecessarily truncate modern long-context models.
- Current `-ngl` supports exact values and modes such as `auto` or `all`. The local UI is integer-oriented.
- Current flash attention option is documented as `--flash-attn [on|off|auto]`; local code appends bare `-fa`.
- Current server supports API key, API key files, SSL cert/key, metrics, slots, sleep idle, models preset, media path, reasoning format, reasoning on/off/auto, and reasoning budget. Local nodes expose only a small subset.

Recommendation:

- Do not expose every llama-server option.
- Update the defaults and contract-sensitive options first: context size, router autoload, auth/profile support, progress, sleep/unload, and model identity.

### `cache_prompt` is not chat memory

Current llama-server docs describe `cache_prompt` as a KV-cache reuse feature for a previous request when there is a common prefix. It is not persistent conversational memory.

Observed local code:

- `nodes/basic_prompt.py` builds a fresh `messages` array per execution and maps `keep_context` to `"cache_prompt": keep_context`.
- `nodes/adv_prompt.py` and `nodes/advpp_prompt.py` use the same pattern.
- The local README/tooltips historically frame `keep_context` as conversation context.

Impact:

- Users may believe the node remembers prior turns when it does not.
- Cached-prefix behavior may improve speed but can also create confusing nondeterminism if presented as memory.

Recommendation:

- Rename or relabel this as "reuse prompt cache" or similar.
- If true conversation history is desired, make it an explicit new node or mode with visible state and reset semantics.

### Structured output docs and implementation disagree

A reliability sub-agent flagged structured output as wrong based on README examples. The main review reconciled that against current source and tests.

Observed evidence:

- Current llama.cpp server README shows a flat example shaped like `{"type":"json_schema","schema":...}`.
- Current server implementation and unit tests read the nested shape `{"type":"json_schema","json_schema":{"schema":...}}`.
- The dirty `ADV++` structured output path appears closer to the current implementation/tests than to the current README example.

Impact:

- This is not a confirmed local bug, but it is a contract-risk area.

Recommendation:

- Add contract tests against the minimum supported llama-server build and a current build.
- Support both JSON object mode and JSON schema mode where practical.
- Document the minimum llama-server version for structured outputs.

## Ecosystem Alternatives And Adjacent Workflows

The ecosystem now contains many ways to use LLMs inside or beside ComfyUI. The practical question is not "can this project call an LLM?" It is "why should a user choose this node?"

An expanded local-only comparison from an end-user perspective is available at `docs/research/local-only-comfyui-llm-options-user-guide-2026-07-10.md`. It separates model runtimes, graph bridges, prompt assistants, session systems, prompt optimizers, caption specialists, and recipe managers, then recommends practical interim trial paths for this workstation.

A later source-level comparison with LLM Party is available at `docs/research/comfyui-llamacpp-vs-llm-party-deep-comparison-2026-07-10.md`. It distinguishes Party's Registry-active, flagged Registry, current-main, and only_api variants; compares user outcomes and architecture; and explains why Party exceeds this project's application breadth without replacing its focused llama.cpp control plane.

| Tool or approach | Current signal | What it covers | What it does not replace |
| --- | ---: | --- | --- |
| Core ComfyUI `TextGenerate` and prompt enhancer nodes | First-party, current | Built-in local text generation and LTX prompt enhancement | External llama-server lifecycle, router profiles, GGUF model management |
| Official OpenAI/Anthropic/OpenRouter/ByteDance partner nodes | First-party docs, current | Cloud/API LLM workflows | Private local llama.cpp, local VRAM handoff, GGUF server control |
| `comfyui_LLM_party` | Registry 121,456 downloads, 2,298 stars | Large all-in-one LLM, agent, tools, provider ecosystem | Lightweight focused llama-server wrapper |
| `ComfyUI-Prompt-Assistant` | GitHub 2,091 stars, GPL-3.0, pushed 2026-04-25 | Rich prompt UX, prompt organization, helper workflows | llama-server lifecycle and router control |
| `comfyui-ollama` | Registry 413,219 downloads, 849 stars | Ollama-backed local LLM workflows | Direct llama.cpp server, GGUF/router specifics |
| `ComfyUI-llama-cpp_vlm` | Known llama-cpp-python VLM approach | In-process llama-cpp-python vision | Avoiding build/dependency conflicts |
| `ComfyUI-LLM-Session` | Registry 7,420 downloads, 27 stars | Persistent sessions and unload concepts | Thin external llama-server focus |
| `Simple LlamaCPP Client` style nodes | Small but close competitor | Thin external llama-server client, JSON mode, API key | Managed server lifecycle and richer Comfy integration |
| llama-swap based nodes | Reddit/GitHub signal | Hot-swap llama.cpp models, unload after generation | Native Comfy model discovery and managed router UX |
| Captioning/VLM nodes such as Florence2, JoyCaption, Qwen VL workflows | Strong Registry and workflow signal | Image/video to prompt, dataset captioning | General llama-server text generation and lifecycle |
| Non-node alternatives: Ollama, LM Studio, llama-swap, Open WebUI, LocalAI, vLLM | Broad external ecosystem | Server management, UI, multi-backend serving | Direct Comfy workflow integration |

Inference:

- A broad provider/agent suite would compete poorly against existing all-in-one nodes.
- A focused llama-server client has a plausible niche if it is reliable, current, installable, and clear about when it manages a server versus talks to an existing one.

## User And Community Evidence

Community evidence was used to understand workflows and pain points, not to settle technical contracts.

Observed themes:

- Users want prompt enhancement, but generic enhancement that drops original details is criticized. The node should preserve prompt fidelity and make transformations visible.
- Local LLM generation can look frozen during long thinking/generation. Users value visible progress, reasoning/thinking display, and clear status.
- VRAM handoff matters. Community posts explicitly value unloading local LLMs after generation on limited VRAM systems.
- Users keep asking how to install or replace missing llama.cpp nodes from shared workflows. Registry/Manager discoverability matters.
- Users like llama.cpp/llama-swap style external serving because it avoids Python dependency conflicts inside ComfyUI.
- Prompt-generation demand includes local/free/private and NSFW-capable workflows, but broad prompt catalog/social features would be scope creep for this project.

Representative community sources:

- `https://www.reddit.com/r/comfyui/comments/1r5ik1p/anyone_having_much_luck_with_incorporating_local/`
- `https://www.reddit.com/r/comfyui/comments/1rorlmw/made_a_comfyui_node_to_textvision_with_any/`
- `https://www.reddit.com/r/comfyui/comments/1su3fq2/is_there_a_comfyui_plugin_to_use_llamaserver_as_a/`
- `https://www.reddit.com/r/comfyui/comments/1rp4lcj/llama_cpp_node_issue/`
- `https://www.reddit.com/r/comfyui/comments/1sqe6xz/missing_node_llama_cpp_instruct_adv/`
- `https://www.reddit.com/r/comfyui/comments/1pkrp5d/i_fell_in_love_with_qwen_vl_for_captioning_but_it/`
- `https://www.reddit.com/r/comfyui/comments/1ujnkfj/diffusiongemma_director_assistant_nodes_for/`
- `https://www.reddit.com/r/comfyui/comments/1u70x65/10eros_tenstrip_ltx23_workflow_with_previews/`
- `https://www.reddit.com/r/comfyui/comments/1tl20bw/need_advice_on_what_direction_to_take_with_comfyui/`
- `https://www.reddit.com/r/comfyui/comments/1lo1415/is_there_any_prompt_generator_for_comfyui/`

## Local Compatibility And Gap Audit

### High-signal local observations

- `server_manager.py:286-297` kills every process whose process name contains `llama-server`.
- `server_manager.py:403-415` starts `llama-server` with `stdout=PIPE` and `stderr=STDOUT`, but no normal reader drains the pipe.
- `server_manager.py:75-77` and `server_manager.py:121-123` omit several effective config fields from restart comparison.
- `server_manager.py:733-756` probes `POST /models` before `/models/load`.
- `model_manager.py:10-29` hardcodes ComfyUI root as two directories above this package and creates `models/LLM/gguf`.
- `model_manager.py:74-78` joins user-facing model names into paths without a containment check.
- `nodes/basic_prompt.py:180-198` maps `keep_context` to `cache_prompt` while sending only current messages.
- `streaming_client.py:70-100` documents an overall timeout but only passes connect/read timeouts to `requests`.
- `streaming_client.py:159-224` breaks on `[DONE]` if present but can still return success after partial content if the stream ends without `[DONE]`.
- `web/adv_prompt.js:51` and `web/adv_prompt.js:74` turn a saved `0` image count into `2`.
- `web/prompt_output.js:19-23` uses deprecated `inputEl` access and serializes a display widget.
- `nodes/__init__.py` imports untracked dirty modules, so a naive tracked-only commit would break imports.

### Confirmed Resolved Or Stale Items

Do not rehash as primary next work without fresh contrary evidence:

- The project already has a Prompt Output node and frontend extension in committed history.
- Basic prompt variety support was already improved in prior commits through presence/frequency penalties and seed behavior.
- ADV prompt vision support exists in both single-model and router paths, but runtime compatibility still needs proof under current ComfyUI and llama-server.
- The dirty worktree already attempts to add Model Info, Token Count, Structured Output, and shared helper logic. The question is not whether those ideas exist, but whether they are correct, complete, tested, and release-ready.

## P0 Findings

No P0 findings identified.

Rationale:

- The review found serious P1 release blockers, but no evidence of an immediate active outage, data loss, credential leak, or irreversible external action.
- The global `llama-server` kill behavior is severe, but it requires invoking lifecycle actions. It is ranked P1 because it can kill unrelated user processes and violates Comfy Registry expectations.

## P1 Findings

### P1.1 - Managed llama-server lifecycle can interfere with unrelated user processes

Observed:

- `server_manager.py` kills every process whose name contains `llama-server`, not just the process this node started.
- Server stdout is piped but not drained during normal operation.
- Lifecycle methods have no obvious locking around process state.
- Config hashes omit fields that materially affect the server command.

Evidence:

- `server_manager.py:286-297`, `server_manager.py:403-415`, `server_manager.py:75-77`, `server_manager.py:121-123`
- Comfy Registry standards expect custom nodes not to interfere with other nodes or user systems.

Impact:

- A ComfyUI user could lose an unrelated llama-server session started outside this node.
- Undrained stdout can deadlock long-running or chatty server processes.
- Changed settings can fail to trigger a restart because the hash does not include all effective options.

Recommended fix:

- Track only owned child process identity and launch metadata.
- Remove broad process-name killing from normal start/stop paths.
- Drain or redirect stdout/stderr safely.
- Add lifecycle locking.
- Include all effective server options in restart comparison.
- Expose a manual "recover orphan owned process" path only if ownership can be proven.

Suggested proof:

- Unit tests for config hash coverage.
- Process ownership test using a fake `llama-server` executable.
- Runtime smoke where an unrelated `llama-server` process survives node start/stop.

### P1.2 - llama-server router and model endpoint contracts are stale

Observed:

- Current llama-server docs use `/models/load` and `/models/unload`.
- Current docs reserve `POST /models` for model download.
- Current docs route GET endpoints such as `/props` by `?model=`.
- Local `load_model()` tries `POST /models` first.
- Dirty model info/token count paths do not yet prove correct router model routing.

Evidence:

- `server_manager.py:733-756`
- Current llama.cpp server README and source/tests in the 2026-07-10 reference checkout.

Impact:

- Router mode can call the wrong endpoint, trigger unexpected behavior, or fail against current llama-server.
- Model info and token counting can report wrong data or use wrong context in router mode.

Recommended fix:

- Treat current `/models/load`, `/models/unload`, `/models?reload=1`, `/models/sse`, `/props?model=...`, and body-routed POST endpoints as the primary contract.
- Keep compatibility fallbacks only where they are known safe.
- Add contract tests against at least the minimum supported llama-server build and a current build.

Suggested proof:

- Mock-server tests for all router lifecycle endpoints.
- Live smoke with current llama-server router mode and at least two models.

### P1.3 - Model discovery bypasses ComfyUI model path configuration

Observed:

- `model_manager.py` assumes the package lives exactly at `ComfyUI/custom_nodes/comfyui-llamacpp`.
- It creates and scans `ComfyUI/models/LLM/gguf`.
- It does not appear to use ComfyUI `folder_paths`, base-directory, models-directory, or extra model paths.
- It does not enforce containment when resolving a selected model path.

Evidence:

- `model_manager.py:10-29`, `model_manager.py:74-78`

Impact:

- Portable installs, alternate custom node paths, extra model paths, and desktop installs can fail or see the wrong model set.
- A malformed model value could resolve outside the intended directory.

Recommended fix:

- Register or query a proper LLM/GGUF folder path through ComfyUI path APIs.
- Respect extra model paths.
- Add containment checks.
- Avoid creating directories as a side effect of simple dropdown discovery unless documented.

Suggested proof:

- Tests for default, alternate base directory, extra model path, subdirectory model, and traversal input.
- Runtime smoke in a nonstandard custom node location.

### P1.4 - Dynamic image inputs need current ComfyUI compatibility proof

Observed:

- JS adds `image_1` through `image_10` sockets dynamically.
- Python V1 `INPUT_TYPES` does not explicitly declare those image inputs.
- Current V1 behavior may require an open optional input mapping pattern for dynamic inputs.
- Saved `image_amount=0` reloads as `2` because of `value || 2`.

Evidence:

- `web/adv_prompt.js:24-26`, `web/adv_prompt.js:51`, `web/adv_prompt.js:74`
- Same pattern exists for ADV++.
- Current ComfyUI docs for inputs and V3 dynamic behavior.

Impact:

- Images may appear connected in the UI but not reach Python execution.
- Existing saved workflows can mutate sockets on load.
- This directly affects the project's VLM value proposition.

Recommended fix:

- Add the correct V1 dynamic optional input acceptance pattern, or use a verified supported current frontend/backend pattern.
- Fix zero handling with nullish/default-aware logic.
- Add saved-workflow fixtures covering 0, 1, and 10 image sockets.

Suggested proof:

- ComfyUI runtime smoke where Python receives each connected image input.
- Saved workflow load/reload tests under current frontend.

### P1.5 - `keep_context` misrepresents `cache_prompt`

Observed:

- Prompt nodes send only the current messages each execution.
- `keep_context` maps to llama-server `cache_prompt`.
- Current llama-server docs describe `cache_prompt` as KV-cache prefix reuse, not chat memory.

Evidence:

- `nodes/basic_prompt.py:180-198`
- `nodes/adv_prompt.py:251-270`
- Current llama.cpp server README.

Impact:

- Users can misunderstand the node and build workflows expecting memory that is not there.
- Debugging repeated or stale outputs becomes harder.

Recommended fix:

- Rename the UI field to prompt-cache reuse language.
- Update README/tooltips.
- If true history is needed, add it as a separate explicit history node or mode with reset and max-turn controls.

Suggested proof:

- Documentation examples showing cache reuse versus explicit history.
- Tests proving two executions do not silently inherit prior user messages.

### P1.6 - The dirty feature bundle is not release-ready as an atomic change

Observed:

- Dirty `nodes/__init__.py` imports untracked new modules.
- Dirty changes add meaningful features but have no test coverage or runtime proof.
- Basic prompt dirty changes insert widgets before existing seed/keep_context fields, which can corrupt positional widget values in saved workflows.
- ADV++ dirty changes mix static and dynamic image socket changes.
- Structured output shape matches current source/tests better than README examples, so it needs contract testing rather than assumption.

Evidence:

- Current `git status --short`
- Dirty source inspection.
- ComfyUI node replacement docs note positional widget value concerns.
- Current llama.cpp README/source/test mismatch for response format.

Impact:

- A tracked-only commit can break imports.
- A naive dirty bundle commit can break existing workflows.
- Users may get incorrect structured output assumptions depending on llama-server build.

Recommended fix:

- Split the dirty bundle into small reviewable patches after this research goal.
- Commit untracked dependencies with any tracked importer changes.
- Preserve existing widget order or provide explicit node replacement/migration.
- Add contract tests before release.

Suggested proof:

- Existing workflow fixture from the current release loads with identical widget semantics.
- `python -m compileall`, JS syntax check, and runtime smoke pass after each slice.

### P1.7 - There is no automated compatibility evidence

Observed:

- No tests, CI, lint, or build setup is configured.
- The prior familiarization pass performed static checks manually, but no reusable test harness exists.
- Current platform and llama-server drift is large.

Evidence:

- Project KB and repo inspection.
- Static checks from prior pass: Python AST parse, JS `node --check`, JSON/TOML parse, `git diff --check`.

Impact:

- Every compatibility claim remains manual.
- Future refactors can break saved workflows or router endpoints silently.

Recommended fix:

- Add a minimal test harness around pure functions, server manager command generation, model path resolution, stream parsing, and router endpoint selection.
- Add fixtures for saved workflows.
- Add a manual runtime smoke checklist until full integration automation is practical.

Suggested proof:

- CI or local script that runs the deterministic test suite.
- Recorded smoke matrix for current ComfyUI plus current/minimum llama-server.

### P1.8 - Registry and install surface are missing

Observed:

- The project is not in the Comfy Registry.
- No license is detected by GitHub.
- Metadata and README likely lag the current intended feature set.

Evidence:

- `https://api.comfy.org/nodes/comfyui-llamacpp` returns 404 on 2026-07-10.
- GitHub metadata shows `license: null`.
- Registry docs define publishing, specifications, and standards.

Impact:

- Shared workflows are harder to install.
- Competing nodes are much easier to discover.
- Registry acceptance could be blocked by lifecycle interference, missing docs, or missing license.

Recommended fix:

- After compatibility fixes, add a license if the owner agrees.
- Update pyproject metadata, README, screenshots/examples, and installation instructions.
- Publish to Registry only after lifecycle and compatibility gates pass.

Suggested proof:

- Registry dry-run/check passes.
- Fresh ComfyUI install can install the node and run a minimal workflow.

## P2 Findings

### P2.1 - Streaming status, progress, cancel, and timeout behavior need tightening

Observed:

- The client has a chunk timeout but not a real overall wall-clock deadline.
- llama-server exposes progress/SSE mechanisms the node does not surface.
- Stream success does not require `[DONE]`.

Impact:

- Long local generations can appear frozen.
- Partial output after a broken stream can be treated as success.

Recommended fix:

- Add a hard deadline or clearly label timeout semantics.
- Track `[DONE]` or explicit finish state when available.
- Surface progress/status in node outputs or Comfy progress APIs.
- Preserve interrupt behavior.

### P2.2 - Prompt Output frontend should move to a current preview pattern

Observed:

- The frontend uses deprecated `inputEl`.
- The display value is serialized to preserve tab-switch behavior.

Impact:

- Future frontend changes can break the widget.
- UI-only output can leak into saved workflow/API state.

Recommended fix:

- Prefer current preview APIs such as V3 `ui.PreviewText` when migrating, or use current widget element APIs and explicit serialization flags in V1.

### P2.3 - External/remote server profiles and auth are incomplete

Observed:

- Prompt nodes accept `server_url`, but lifecycle, model dropdowns, load/unload, and API key handling remain local-first.
- Current llama-server supports API keys and remote HTTPS setups.

Impact:

- Users running llama.cpp, llama-swap, LM Studio-compatible, or reverse-proxied servers cannot configure them cleanly.

Recommended fix:

- Add connection profiles: managed local server, existing local server, and remote OpenAI-compatible server.
- Handle auth via environment/profile configuration rather than raw visible API key widgets.
- Make model listing optional and capability-aware.

### P2.4 - Capability discovery should drive UX

Observed:

- Current llama-server exposes props, modalities, slots, metrics, model status, and sleep state.
- Dirty Model Info is a useful direction but not yet contract-proven.

Impact:

- Nodes cannot adapt to text-only versus VLM models, router load state, context size, or sleeping models.

Recommended fix:

- Add a capability/status output that can be wired into workflows.
- Use `/props?model=...`, `/models`, `/health`, and optionally `/metrics` where available.
- Fail visibly when a VLM request is sent to a non-VLM model.

### P2.5 - Prompt enhancement needs fidelity controls, not a giant prompt catalog

Observed:

- Community complaints focus on enhancers losing details or making stuff up.
- Current core/third-party tools already cover broad prompt generation.

Impact:

- A generic "enhance prompt" feature can make outputs worse if it hides edits.

Recommended fix:

- Add templates that preserve original prompt, target a model family, and output both original and enhanced text.
- Consider structured outputs with fields such as `preserved_subjects`, `added_details`, `negative_prompt`, and `final_prompt`.
- Keep templates backend-readable and versioned, not frontend-only.

### P2.6 - Stop sequence and token-ban UX is too lossy

Observed:

- Stop sequences are comma-split.
- Token-ban input cannot naturally represent comma-containing or whitespace-sensitive values.

Impact:

- Users cannot express some legitimate stops or bans.

Recommended fix:

- Support JSON list input or multiline input.
- Document exact semantics for string stops versus token IDs.

### P2.7 - VLM behavior should fail louder

Observed:

- Image tensor conversion failures are logged and skipped.
- Batch handling needs explicit behavior.
- Media support in current llama-server is broader than the local image-only path.

Impact:

- A workflow can silently degrade from multimodal to text-only.

Recommended fix:

- Return a warning/status output when image conversion fails.
- Define batch behavior clearly.
- Park audio/video media support until image routing is proven.

### P2.8 - Packaging metadata and direct dependencies need review

Observed:

- The code imports packages such as `requests`, `psutil`, `numpy`, and `Pillow` paths through image conversion.
- Requirements may not fully declare all direct imports from current dirty and committed code.

Impact:

- Fresh installs can fail with missing packages.

Recommended fix:

- Audit direct imports after deciding the feature slices.
- Keep dependencies minimal and declared.

## P3 Findings

### P3.1 - Full V3 migration is useful but not first

V3 is the long-term direction, but a full migration before stabilizing current functionality would increase risk. Use V3 docs to inform compatibility work now, then migrate later.

### P3.2 - Search aliases and example workflows would improve discoverability

Add search aliases like `LLM`, `llama.cpp`, `GGUF`, `prompt enhancer`, and `VLM`. Add example workflows for basic text, image-to-prompt, structured JSON, router mode, and external server mode after compatibility is proven.

### P3.3 - Native Ollama controls should be parked

Ollama has strong adoption and `comfyui-ollama` is already popular. This project can document Ollama/LM Studio/llama-swap compatibility through external server profiles, but native Ollama pull/keep_alive management is a separate product.

### P3.4 - Persistent chat history should be explicit and optional

True conversation state can be valuable, but it changes workflow determinism. Park it until prompt-cache semantics are fixed and connection profiles exist.

### P3.5 - Tool calls, Responses API, embeddings, RAG, and MCP are later frontiers

Current llama-server supports richer APIs, but implementing them now would expand scope before the basics are reliable.

### P3.6 - Python 3.14 should be watched, not targeted now

Current ComfyUI docs emphasize Python 3.13 support and note custom-node caveats around newer versions. Target 3.12/3.13 first.

## Worth Implementing Now

1. Freeze and reconcile the dirty bundle as future slices, not as one large commit.
2. Fix lifecycle safety: owned process tracking, no global kill, stdout draining, locks, complete config hashes.
3. Update llama-server router contracts and add endpoint contract tests.
4. Replace hardcoded model discovery with ComfyUI path-aware discovery and containment checks.
5. Verify and fix V1 dynamic image sockets plus saved workflow migration.
6. Rename/reframe `keep_context` as prompt-cache reuse, and defer true history unless explicitly added.
7. Add connection profiles for managed local, existing local, and remote OpenAI-compatible servers.
8. Add structured output contract tests and clear minimum llama-server version docs.
9. Add status/progress outputs and clearer failure states.
10. Prepare Registry-ready packaging, license choice, README, examples, and install docs after compatibility gates pass.

These are direct implementation candidates for this project.

## Worth Parking For Later

1. Full V3 node rewrite.
2. Persistent conversation history and session browser.
3. Prompt diff/reviewer UI.
4. Native Ollama pull, keep_alive, and model management.
5. Large prompt style catalogs or target-model prompt packs.
6. Tool calling, Responses API, embeddings, RAG, and MCP workflows.
7. vLLM or multi-backend serving management.
8. Audio/video multimodal request support beyond current image VLM paths.
9. Python 3.14 compatibility work.

These may be useful later, but they should not block compatibility stabilization.

## Not Worth Copying

1. `llama-cpp-python` in-process inference as the main path. It undermines this project's external-server advantage.
2. All-in-one LLM Party scope, including broad provider suites, agents, RAG, TTS, social, and tool ecosystems.
3. A bespoke model router when llama-server router and llama-swap already exist.
4. Cloud-provider SDK sprawl inside this node pack.
5. Raw visible API key widgets in workflows.
6. Giant prompt catalogs that become maintenance liabilities.
7. Agentic workflow generation as a core feature.
8. Frontend runtime bloat or bundled framework code for simple controls.

These are reference patterns at most, not direct implementation candidates.

## Recommended Next Milestone Or Work Block

Recommended milestone: "Compatibility Stabilization and Release Readiness."

Goal:

- Make the current project safe and correct against current ComfyUI and current/minimum llama-server without adding broad new product scope.

Suggested order:

1. Snapshot the current dirty state and split it into proposed feature slices.
2. Add deterministic tests for command generation, config hash, model path resolution, stream parsing, and router endpoint selection.
3. Fix lifecycle ownership and stdout handling.
4. Fix model discovery through ComfyUI path APIs.
5. Update router endpoint logic and model identity handling.
6. Fix dynamic image input behavior and saved workflow compatibility.
7. Clarify prompt-cache semantics.
8. Reintroduce dirty features one at a time behind tests.
9. Run the runtime smoke matrix.
10. Update docs, metadata, license, and Registry readiness.

This is the highest-leverage next work because it turns the repo from a promising but stale local tool into something that can safely accept feature work.

## Suggested Acceptance Gates

Local static gates:

- `python3 -m compileall -q .`
- JS syntax check for every `web/*.js`.
- JSON/TOML parse for `web/templates.json` and `pyproject.toml`.
- `git diff --check`.

Unit/contract gates to add:

- Server command generation for single-model and router mode.
- Config hash coverage for every effective option.
- Model path discovery and traversal containment.
- Router endpoint selection for current llama-server.
- Stream parsing for normal content, reasoning content, errors, missing `[DONE]`, and chunk timeout.
- Structured output payload shape for current and minimum supported llama-server.
- Saved workflow widget order and dynamic image socket fixtures.

Runtime smoke matrix:

- ComfyUI core `v0.27.0` plus frontend package near current stable.
- ComfyUI Desktop current or recent stable.
- Current llama-server from llama.cpp.
- Minimum supported llama-server version, once chosen.
- Python 3.12 and 3.13.
- Single-model mode and router mode.
- Model roots: default, extra model path, and alternate base directory.
- Prompt nodes: Basic, ADV, ADV++, Prompt Output.
- Dirty-feature candidates: Model Info, Token Count, Structured Output.
- Image counts: 0, 1, 2, and 10.
- Existing saved workflow from version 0.2.1.

Release gates:

- No product source imports untracked files.
- README and node help text match actual behavior.
- License is selected and committed.
- Registry metadata validates.
- Fresh install path works without undocumented manual steps.

## What Not To Do Next

- Do not do a full V3 rewrite first.
- Do not commit the full dirty bundle as-is.
- Do not publish to Registry before lifecycle safety is fixed.
- Do not add broad provider, RAG, agent, or prompt-catalog scope before compatibility gates.
- Do not keep presenting `cache_prompt` as conversation memory.
- Do not keep global process killing as a convenience cleanup path.
- Do not treat Reddit sentiment as a technical spec.

## Evidence And Sources

Local commands and evidence:

- `git log -1 --format='%H%n%cd%n%s' --date=short`
- `git status --short`
- `nl -ba server_manager.py`
- `nl -ba model_manager.py`
- `nl -ba nodes/basic_prompt.py`
- `nl -ba nodes/adv_prompt.py`
- `nl -ba streaming_client.py`
- `nl -ba web/adv_prompt.js`
- `nl -ba web/prompt_output.js`
- Prior static checks recorded in the Project KB: Python AST parse, JS `node --check`, JSON/TOML parse, `git diff --check`

Official and primary technical sources:

- `https://docs.comfy.org/custom-nodes/v3_migration`
- `https://docs.comfy.org/custom-nodes/backend/lifecycle`
- `https://docs.comfy.org/custom-nodes/backend/more_on_inputs`
- `https://docs.comfy.org/custom-nodes/backend/node-replacement`
- `https://docs.comfy.org/custom-nodes/js/javascript_overview`
- `https://docs.comfy.org/custom-nodes/js/javascript_hooks`
- `https://docs.comfy.org/custom-nodes/js/javascript_objects_and_hijacking`
- `https://docs.comfy.org/registry/publishing`
- `https://docs.comfy.org/registry/specifications`
- `https://docs.comfy.org/registry/standards`
- `https://docs.comfy.org/tutorials/partner-nodes/openai/chat`
- `https://docs.comfy.org/tutorials/partner-nodes/openrouter/llm`
- `https://github.com/Comfy-Org/ComfyUI`
- `https://github.com/Comfy-Org/ComfyUI_frontend`
- `https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md`
- `https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md`
- `https://github.com/ggml-org/llama.cpp`

GitHub and Registry evidence:

- `https://github.com/Setmaster/comfyui-llamacpp`
- `https://github.com/Comfy-Org/ComfyUI/releases/tag/v0.27.0`
- `https://github.com/Comfy-Org/ComfyUI/compare/8086468d2a1a5a6ed70fea3391e7fb9248ebc7da...master`
- `https://github.com/ggml-org/llama.cpp/compare/10e5b148b061569aaee8ae0cf72a703129df0eab...master`
- `https://github.com/ggml-org/llama.cpp/compare/b7389...master`
- `https://api.comfy.org/nodes/comfyui-llamacpp`
- `https://api.comfy.org/nodes/comfyui-ollama`
- `https://api.comfy.org/nodes/comfyui_llm_party`
- `https://api.comfy.org/nodes/comfyui-llm-session`
- `https://api.comfy.org/nodes/comfyui-if_llm`
- `https://api.comfy.org/nodes/ComfyUI-JoyCaption`

Reference competitor and adjacent projects reviewed:

- `https://github.com/heshengtao/comfyui_LLM_party`
- `https://github.com/yawiii/ComfyUI-Prompt-Assistant`
- `https://github.com/stavsap/comfyui-ollama`
- `https://github.com/kantan-kanto/ComfyUI-LLM-Session`
- `https://github.com/ai-joe-git/comfyui_llama_swap`
- `https://github.com/GlatTissekone/ComfyUI-Llama-Prompt-Generator`
- `https://github.com/RealRebelAI/RebelsPromptEnhancer`

Community evidence:

- `https://www.reddit.com/r/comfyui/comments/1r5ik1p/anyone_having_much_luck_with_incorporating_local/`
- `https://www.reddit.com/r/comfyui/comments/1rorlmw/made_a_comfyui_node_to_textvision_with_any/`
- `https://www.reddit.com/r/comfyui/comments/1su3fq2/is_there_a_comfyui_plugin_to_use_llamaserver_as_a/`
- `https://www.reddit.com/r/comfyui/comments/1rp4lcj/llama_cpp_node_issue/`
- `https://www.reddit.com/r/comfyui/comments/1sqe6xz/missing_node_llama_cpp_instruct_adv/`
- `https://www.reddit.com/r/comfyui/comments/1pkrp5d/i_fell_in_love_with_qwen_vl_for_captioning_but_it/`
- `https://www.reddit.com/r/comfyui/comments/1ujnkfj/diffusiongemma_director_assistant_nodes_for/`
- `https://www.reddit.com/r/comfyui/comments/1u70x65/10eros_tenstrip_ltx23_workflow_with_previews/`
- `https://www.reddit.com/r/comfyui/comments/1tl20bw/need_advice_on_what_direction_to_take_with_comfyui/`
- `https://www.reddit.com/r/comfyui/comments/1lo1415/is_there_any_prompt_generator_for_comfyui/`

## Remaining Uncertainty

- The dynamic image input issue is high confidence from docs/code shape, but still needs live ComfyUI runtime proof.
- Structured output shape is a known docs/source mismatch upstream, not a confirmed local defect.
- Current llama-server CLI flag details should be verified against the exact binary selected for the next release.
- Registry acceptance details can change, so publishing should be checked again immediately before submission.
- Reddit evidence is useful for pain points and demand signals, but not for endpoint or API correctness.

## Bottom Line

`comfyui-llamacpp` still has a viable reason to exist, but not as a broad LLM suite. Its recoverable niche is a small, dependable ComfyUI bridge to external llama.cpp-compatible servers.

The next work should be compatibility stabilization, lifecycle safety, llama-server contract tests, model discovery, dynamic image workflow proof, and Registry readiness. Feature expansion should wait until those gates pass.
