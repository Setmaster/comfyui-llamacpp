# Local-Only ComfyUI LLM Options: An End-User Guide

Date: 2026-07-10

Mode: Research and analysis only

Scope: Local inference and local workflows. Cloud API products are excluded from the recommendations.

## Why This Follow-Up Exists

The project frontier review concluded that the alternatives to `comfyui-llamacpp` are not one direct replacement. That statement needs a user-facing explanation because the alternatives solve different parts of the experience.

Some tools merely connect ComfyUI to a model server. Some add an interactive writing assistant. Some preserve real conversation state. Others are specialized image captioners, prompt optimizers, tag generators, or prompt libraries. Comparing all of them as if they were interchangeable "LLM nodes" hides the decision a user actually needs to make.

This guide asks a different question from the main project review:

> What can a user install and use locally now, while this project is being evaluated and modernized?

The earlier local-options synthesis was logged in the Project KB as a summary. The later answer that directly compared this project's full interaction coverage with the alternatives was copied verbatim from the visible conversation into the same recap, with its provenance labeled as retrieved exact text.

A separate source-level comparison of this project and LLM Party is available at `docs/research/comfyui-llamacpp-vs-llm-party-deep-comparison-2026-07-10.md`.

A focused audit of current ComfyUI, in-process node-pack, and external-runtime VRAM unloading is available at `docs/research/comfyui-local-llm-vram-unloading-audit-2026-07-10.md`. It supersedes any broad reading that all in-process LLM nodes necessarily retain VRAM until restart.

## Executive Recommendation

There is no single universal winner. The best current local-only choice depends on the job:

| User need | Best first option | Local runtime | Main caution |
| --- | --- | --- | --- |
| Use an LLM already present in a compatible Comfy workflow | Core ComfyUI `Generate Text` | ComfyUI model loading | It is not an arbitrary GGUF loader or a session system |
| General LLM calls inside a graph, with explicit VRAM handoff controls | `(Deno) Local LLM Loader`, as a controlled trial | Ollama, LM Studio, llama.cpp, vLLM, or another local endpoint | The feature is new and does not provide a confirmed provider-to-GPU completion barrier |
| Conservative, mature Ollama graph nodes | `comfyui-ollama` | Ollama | No durable file-backed sessions, in-node model-pull workflow, or polished real-time token streaming |
| Interactive prompt writing, translation, tags, and captioning | Prompt Assistant | Ollama | Broad frontend integration can affect large-workflow canvas performance |
| Persistent multi-turn conversations | ComfyUI-LLM-Session | In-process GGUF through `llama-cpp-python` | Native wheel and multimodal handler compatibility |
| Flexible image or video understanding and prompt rewriting | ComfyUI-QwenVL or native `Generate Text` with a supported Qwen encoder | Transformers, GGUF, or Comfy-native loading | Heavier dependencies and VRAM use |
| Fast captions, OCR, detection, grounding, masks | ComfyUI-Florence2 | In-process Transformers | Less flexible than a current general VLM |
| Rich, uncensored training captions | JoyCaption | In-process Transformers or a GGUF implementation | More memory, slower generation, and compatibility friction |
| Anime, Pony, Illustrious, or Danbooru tag expansion | TIPO | Small in-process model | Specialist only, not a general assistant |
| Prompt and generation recipe library | ComfyUI Prompt Manager using Ollama | Ollama | Use Ollama only; its separate managed llama-server mode kills unrelated llama-server processes |

For this workstation, with an RTX 5090 with 32 GB VRAM and 94 GB system RAM, my practical starting stack would be:

1. Use Ollama if a simple local service is preferred, or LM Studio if a graphical model browser and explicit server controls are preferred.
2. Start with a current 4B to 9B instruction model. Try `qwen3.5:9b` as the single-model starting hypothesis if the selected local runtime tag accepts image input; otherwise pair a Qwen 3.5 text model with Qwen3-VL 4B or 8B for vision.
3. Try `(Deno) Local LLM Loader` for graph-native use, with model unloading enabled. For a strict single-GPU trial, set ComfyUI model unloading to `Always`, then verify provider status and driver-visible free memory rather than trusting request success alone. Treat this as a controlled trial because the feature is new.
4. If the real need is an assistant beside text fields rather than another graph node, try Prompt Assistant with the same Ollama model instead.
5. Keep `comfyui-ollama` as the more established fallback if Deno's new node proves unstable.
6. Add Florence2, QwenVL, JoyCaption, or TIPO only when their specialized output is actually needed.

This recommendation is not a live benchmark. No alternative was installed into the user's main ComfyUI environment during this research task.

## The Missing Mental Model: Alternatives Occupy Different Layers

The word "alternative" currently covers at least seven layers:

| Layer | What it does | Examples | What it does not automatically provide |
| --- | --- | --- | --- |
| 1. Model runtime | Loads the model and performs inference | Ollama, LM Studio, llama-server, llama-swap, in-process Transformers, `llama-cpp-python` | A useful ComfyUI workflow or prompt UX |
| 2. ComfyUI bridge | Converts node inputs into runtime requests and returns strings | Deno Local LLM Loader, `comfyui-ollama`, generic OpenAI-compatible nodes, `comfyui-llamacpp` | Persistent chat, prompt libraries, or specialized prompting |
| 3. Session system | Preserves messages and summaries across executions | ComfyUI-LLM-Session, parts of LLM Party | A polished text-editing assistant or caption specialist |
| 4. Prompt assistant | Helps the user write, revise, translate, and organize prompts | Prompt Assistant | General model lifecycle or durable conversation state |
| 5. Prompt optimizer | Encodes rules for a target diffusion or video model | Eric's Prompt Enhancers, TIPO, model-specific prompt templates | A general chat interface or arbitrary vision tasks |
| 6. Vision or caption specialist | Converts images, video, or documents into text or structured visual data | Florence2, JoyCaption, QwenVL | Model-server management or interactive prompt history |
| 7. Asset and recipe manager | Stores prompts and generation metadata for reuse | ComfyUI Prompt Manager | The smallest or safest inference layer |

The layers can be combined. For example:

- Ollama can host the model.
- Deno Local LLM Loader can call it from a graph.
- Prompt Assistant can use the same Ollama installation for interactive editing.
- Florence2 can remain a separate specialist for fast OCR and bounding boxes.

That is not needless duplication if each component has a distinct job. It becomes unnecessary complexity when several large node packs all install their own model stacks merely to return one prompt string.

Another important distinction is that the transport node does not create most of the output quality. Output quality usually depends more on the selected model, quantization, system instruction, examples, context, and whether the task matches the model. A polished node can make a mediocre prompt strategy easier to use, but it cannot turn it into a strong one by itself.

## First Decision: Where Should the Model Run?

### External local runtime

Examples: Ollama, LM Studio, llama-server, llama-swap.

Advantages:

- Keeps native model libraries and most model dependencies outside ComfyUI's Python environment.
- One runtime can serve several ComfyUI plugins or non-Comfy applications.
- Model inventory and runtime upgrades are independent from ComfyUI custom-node upgrades.
- Failures are usually easier to isolate than an in-process CUDA, Transformers, or Python package conflict.
- Explicit model unload, time-to-live, or server stop controls are possible.

Costs:

- The runtime and ComfyUI are independent VRAM owners.
- One process does not automatically know that the other needs memory.
- If the Comfy node does not request unload, the LLM may remain resident and block a diffusion or video model.
- There is another service, port, configuration, and log surface to understand.

For a single-GPU ComfyUI workstation, this is usually the safer temporary architecture if the bridge exposes reliable unload behavior. It reduces Python dependency risk, but only solves VRAM contention when lifecycle controls are actually used.

### In-process `llama-cpp-python`

Examples: ComfyUI-LLM-Session, Simple Qwen3-VL GGUF, GGUF modes in several VLM packs.

Advantages:

- Direct GGUF use without managing a separate HTTP service.
- A node can expose model internals and cache state more directly.
- Session-specific features can be tightly coupled to the inference object.

Costs:

- Wheel compatibility depends on Python, CUDA, GPU architecture, llama.cpp age, and model handlers.
- Several current GGUF multimodal ComfyUI integrations document a third-party JamePeng build or a source build rather than the ordinary PyPI package.
- Native library failures enter ComfyUI's process.
- Model release and cache cleanup depend on each plugin's implementation.
- Several projects and community reports describe models remaining resident or caches failing after updates.

This route is justified when the node provides a real differentiator, such as persistent sessions, direct current-model GGUF support, or a specialist caption mode. It is a poor default merely to avoid running a small local server.

### In-process Transformers

Examples: QwenVL, Florence2, JoyCaption.

Advantages:

- Often the most direct path to a model's official architecture and task-specific outputs.
- Integrates naturally with tensors, images, boxes, masks, and other Comfy values.
- Some nodes use ComfyUI model-management abstractions and can participate in its memory handling.

Costs:

- Transformers, Torch, attention libraries, quantization libraries, NumPy, Triton, and model-specific packages can conflict across custom nodes.
- Model downloads can be large and spread across different directories or caches.
- A current general VLM can consume memory needed by the main image or video pipeline.

This is most sensible for Florence2, JoyCaption, or another task-specific model where the structured visual outputs matter. It is less attractive for a generic one-shot text rewrite.

### Comfy-native text generation

Current ComfyUI includes a first-party `Generate Text` node. It accepts a Comfy `CLIP` object and can pass text, image, video, audio, sampling, and thinking inputs when the connected encoder supports them. ComfyUI also includes `Generate LTX2 Prompt`, with first-party instructions for LTX text-to-video and image-to-video prompting. See [ComfyUI's current implementation](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy_extras/nodes_textgen.py) and the [built-in node documentation](https://docs.comfy.org/built-in-nodes/TextGenerate).

This is the lowest-dependency experiment when the workflow already loads a compatible generative encoder. It is not a universal server client: it does not browse arbitrary GGUF files, run a separate model endpoint, preserve sessions, or manage a prompt library.

## Second Decision: What Experience Do You Want?

### 1. Core ComfyUI `Generate Text`

Position: First-party model reuse inside a workflow.

Why use it:

- No third-party LLM custom node is required.
- It can reuse a compatible text or vision encoder already loaded for the workflow.
- It is the path most likely to align with current ComfyUI execution and memory conventions.
- The LTX2 variant already contains a fidelity-oriented first-party video prompt strategy.

What it feels like:

- A normal graph node, not a chat application.
- The user writes an instruction, connects the correct model object, and receives a string.
- There is no backend profile screen, model server browser, conversation browser, or prompt library.

When to choose it:

- The current workflow already has a supported Qwen, Gemma, LTX, or similar generative encoder.
- The goal is a caption or rewrite within that same graph.
- Avoiding a duplicate LLM model in VRAM matters.

When not to choose it:

- The desired model is an arbitrary GGUF file.
- The model should be shared by several applications.
- Persistent chat, model hot-swapping, saved provider profiles, or explicit endpoint control is required.

Verdict: Try this before installing another model stack when the workflow already has the right encoder.

### 2. `(Deno) Local LLM Loader`

Position: A current, graph-native bridge to several external local runtimes.

The project supports Ollama, LM Studio, llama.cpp, vLLM, and custom local endpoints. Its design includes provider model discovery, streaming thinking and result previews, request stopping, optional image input, prompt batches, model memory choices, explicit unload controls, and policies for unloading ComfyUI models before an LLM call. It defaults to loopback-only access and requires an explicit allowlist for trusted LAN hosts. The current feature is documented in the [Deno node repository](https://github.com/Deno2026/comfyui-deno-custom-nodes).

Why it stands out:

- It treats VRAM handoff as a first-class workflow problem.
- It distinguishes "unload after run," temporary retention, and keeping a model loaded.
- It knows provider-specific lifecycle behavior instead of treating every endpoint as identical.
- It provides live thinking and result previews and a stop action.
- It includes a reviewer gate that can pass or block image or audio outputs based on review text.
- Its source and tests show substantial attention to provider selection, saved-workflow migration, preview state, reviewer behavior, and unload and stop controls.

What it does not do:

- It does not install or start the local runtime for the user.
- It does not pull or download models.
- It does not preserve a real multi-turn conversation.
- It returns one main string output; thinking is primarily a UI preview rather than a separate graph output.
- It does not expose a general user-supplied JSON Schema contract.
- Its Comfy-side cleanup unloads registered `ModelPatcher` models, but cannot clear unmanaged custom-node objects, execution-cache references, module globals, or native contexts owned by other packs.
- Provider unload calls are not followed by a universal VRAM-completion poll. Ollama, llama.cpp router, and some vLLM modes can acknowledge a request before memory recovery is complete.

Main uncertainty:

The Local LLM feature was introduced in early July 2026. The surrounding node pack has meaningful adoption, but this specific feature does not yet have months of field evidence. It is also part of a broad utility pack rather than a dedicated LLM package.

Verdict: The most deliberate reviewed controlled trial for a general local graph client, especially on a single GPU where unload behavior matters. It is not yet a proven strict handoff barrier, so keep a mature fallback and measure real VRAM recovery.

### 3. `comfyui-ollama`

Position: The established general-purpose Ollama bridge.

The package provides connectivity, options, generation, chat, and context nodes. It discovers installed models, supports configurable URLs, image inputs, JSON mode, thinking options, `keep_alive`, generation context, and process-memory chat history. It has the strongest adoption signal among focused local LLM bridge nodes, with more than 400,000 Registry downloads at retrieval. See the [project repository](https://github.com/stavsap/comfyui-ollama) and [Registry entry](https://api.comfy.org/nodes/comfyui-ollama).

Why use it:

- Ollama installation and model management remain separate from ComfyUI.
- The node surface is focused and understandable.
- It is more established than the newest local-endpoint clients.
- `keep_alive` lets a workflow request immediate unload or retention.

Limits and current reports:

- Chat history is process memory rather than durable named sessions.
- There is no in-node model pull workflow.
- Generation is not presented as true live token streaming.
- JSON mode is not the same as enforcing an arbitrary user JSON Schema.
- Open reports include a wrong URL freezing the UI, multiple-image limitations, images not reaching some models, structured-output requests, and audio requests.
- Current source exposes a chat thinking input, but the inspected request path did not clearly forward it. That needs a live check before relying on it.

Verdict: The conservative focused choice if the user is happy to standardize on Ollama and does not need durable sessions or specialized prompt UX.

### 4. Prompt Assistant

Position: An interactive writing layer attached to ComfyUI's editing experience.

Prompt Assistant is not primarily a replacement inference engine. It adds assistant actions around text and image nodes and supplies prompt enhancement, translation, image captioning, beta video captioning, reusable tags and phrases, prompt history, undo and redo, node-documentation translation, model discovery, streaming interaction effects, and Ollama auto-unload. Its user configuration is stored under `ComfyUI/user/default/prompt-assistant`, outside the plugin directory. See the [English project documentation](https://github.com/yawiii/ComfyUI-Prompt-Assistant/blob/main/README.en.md) and [Registry entry](https://api.comfy.org/nodes/prompt-assistant).

Why a user may prefer it:

- It solves the moment of writing rather than requiring a separate enhancement subgraph everywhere.
- Tags, phrases, rules, histories, and translation are available as authoring tools.
- Enhancement, translation, and captioning can have different local model configurations.
- It is actively maintained for current ComfyUI frontend and V3/Node 2.0 behavior.
- Its adoption is much stronger than most specialized prompt enhancers.

What it is not:

- It is not a persistent conversational assistant.
- It is not a generic runtime manager.
- It does not provide a typed structured-output engine.
- User rules, service configuration, history, and tags do not automatically travel with a shared workflow.

Main caution:

This is a broad frontend extension that mounts controls around many nodes. An open issue attributes severe canvas-panning slowdown in a roughly 200-node workflow to per-node assistant positioning work. The package has repeatedly fixed subgraph and frontend mounting behavior, which shows active maintenance but also reveals the integration surface.

Local-only configuration:

- Configure only Ollama or an explicitly local compatible endpoint.
- Leave cloud-provider credentials empty.
- Verify the configured base URL resolves to loopback.
- Test a representative large workflow before relying on it.

Verdict: The best current option if the desired experience is "help me write and revise prompts while I use ComfyUI," rather than "give me another LLM graph node."

### 5. ComfyUI-LLM-Session

Position: Persistent local conversation and model-to-model dialogue.

This project loads GGUF models through `llama-cpp-python` and provides file-backed session IDs, resumable transcripts, history summaries, disk and runtime caches, image batches, model-to-model dialogue, and an explicit unload node. Version 1.3.0 was released on 2026-07-05, and the repository includes tests and compatibility documentation. See the [project repository](https://github.com/kantan-kanto/ComfyUI-LLM-Session) and [Registry entry](https://api.comfy.org/nodes/comfyui-llm-session).

Why it is different:

- Session history is a real durable product feature, not a misleading alias for llama.cpp's `cache_prompt`.
- A conversation can be resumed across workflow executions.
- History summarization is designed for longer sessions.
- It supports user-to-model and model-to-model conversation patterns.

Costs:

- Several supported multimodal paths may require a specific JamePeng `llama-cpp-python` wheel matched to Python, CUDA, and hardware.
- The model runs inside ComfyUI's process.
- Media itself is not stored in conversation history.
- There is no documented general schema-constrained output mode.
- Streaming is primarily console heartbeat or token output, not a polished live node chat window.
- Current issues include slow execution under some history/cache conditions and image-handler setup confusion.

Verdict: Choose it when persistent state is the point. Do not accept its installation complexity for a one-shot prompt rewrite.

### 6. ComfyUI-QwenVL

Position: A flexible local vision-language workstation inside ComfyUI.

The current package supports Qwen3-VL and Qwen2.5-VL for image and video analysis, with standard and advanced nodes, preset and custom prompts, automatic model download, 4-bit, 8-bit, FP16 and some FP8 paths, progress reporting, memory controls, Transformers and GGUF backends, and dedicated text-only prompt enhancer nodes. See the [project repository](https://github.com/1038lab/ComfyUI-QwenVL) and [Registry entry](https://api.comfy.org/nodes/ComfyUI-QwenVL).

Why use it:

- One current model family can caption images, inspect video frames, answer arbitrary visual questions, and rewrite prompts.
- It supports instructions rather than only fixed caption tasks.
- It has both accessible and advanced node surfaces.
- The 1038lab package has substantial adoption and Blackwell-oriented attention handling.

Costs:

- The Transformers path adds a meaningful Python and CUDA dependency surface.
- The GGUF path adds `llama-cpp-python` wheel complexity and current vision-handler requirements.
- Model auto-download can consume substantial disk space.
- Keeping the model loaded can conflict with diffusion or video stages.
- Community reports include both excellent results and substantial setup or performance variance.

Verdict: The best broad VLM category when custom instructions and quality matter more than minimal installation. On this hardware, begin with 4B or 8B/9B-class models and disable model retention when the next graph stage needs most of the GPU.

### 7. ComfyUI-Florence2

Position: Fast, low-resource visual utility.

Florence2 covers captioning, detailed captioning, dense region captioning, OCR, OCR with regions, object detection, segmentation, phrase grounding, DocVQA, and PromptGen fine-tunes. Depending on the task, it can return text, parsed JSON, regions, masks, and annotated images. It has the largest adoption signal among the reviewed captioning packages, with more than 1.5 million Registry downloads at retrieval. See the [project repository](https://github.com/kijai/ComfyUI-Florence2) and [Registry entry](https://api.comfy.org/nodes/comfyui-florence2).

Why use it:

- It is much smaller than a current general VLM.
- It is well suited to batch captioning, OCR, boxes, masks, and grounding.
- Its structured visual outputs are directly useful elsewhere in a graph.
- PromptGen fine-tunes provide an image-to-prompt path.

Limits:

- It is less capable than a current general VLM at nuanced instructions, reasoning, or rich creative prose.
- Ordinary caption tasks do not accept arbitrary user instructions in the same way a chat VLM does.
- Its long-lived compatibility surface has accumulated current Transformers, device, and model-discovery issues.

Verdict: The first specialist to try for speed, OCR, localization, and bulk utility captioning. It is not a general LLM replacement.

### 8. JoyCaption

Position: Rich image captions for training and curation.

JoyCaption is designed for detailed, open, and uncensored descriptions across photographs, art, anime, furry, SFW, and NSFW material. It offers descriptive, straightforward, Stable Diffusion, booru-like, art-critic, product, and social styles. The canonical project and the 1038lab implementation offer different tradeoffs; the latter adds GGUF and batch file-saving utilities. See the [canonical JoyCaption project](https://github.com/fpgaminer/joycaption), [canonical ComfyUI node](https://github.com/fpgaminer/joycaption_comfyui), and [1038lab implementation](https://github.com/1038lab/ComfyUI-JoyCaption).

Why use it:

- It targets training-caption quality rather than generic chat.
- It provides explicit caption types and length or instruction controls.
- It is useful for LoRA and dataset preparation where a human will review captions.

Costs:

- The canonical full-precision path is heavy; its documentation places native memory demand around 17 GB with 24 GB recommended.
- Quantized routes reduce memory at some quality or compatibility cost.
- Open reports cover freezes, repeated-session behavior, quantization problems, GPU use, and model discovery.
- It returns prose strings rather than a guaranteed typed record.

Verdict: Strong for rich dataset captions, not for general prompt rewriting. A dedicated caption-curation application can be better than ComfyUI for reviewing thousands of files.

### 9. TIPO and DanTagGen

Position: Small specialist models for tag expansion.

TIPO accepts natural language and tags and expands categories such as character, copyright, artist, quality, rating, and general tags. It supports banned-tag patterns, deterministic seeds, output-length controls, and configurable prompt formatting. Models are roughly 0.1B to 0.5B parameters, dramatically smaller than a general 4B to 9B assistant. See the [TIPO extension](https://github.com/KohakuBlueleaf/z-tipo-extension), [Registry entry](https://api.comfy.org/nodes/z-tipo-extension), and [TIPO-500M model](https://huggingface.co/KBlueLeaf/TIPO-500M).

Why use it:

- A small model trained for Danbooru-style structure can outperform a generic assistant at that narrow syntax.
- It consumes far less memory and starts faster.
- It exposes category and banning controls that map to the target task.

Limits:

- No image understanding, general conversation, or video direction.
- Installation can still involve `llama-cpp-python`; current issue reports include missing libraries and import failures.
- The model license and extension license are separate and should both be checked.

Verdict: The right specialist for Pony, Illustrious, anime, and Danbooru-style workflows. Do not use a larger generic LLM merely because it sounds more capable.

### 10. ComfyUI Prompt Manager

Position: Prompt and complete generation-recipe organization.

Prompt Manager combines text enhancement and image analysis with a prompt library, thumbnails, trigger words, LoRA stacks, metadata extraction, and recipes that can preserve prompts, models, LoRAs, sampler settings, dimensions, and seeds. It supports Ollama and a managed llama-server path. See the [project repository](https://github.com/FranckyB/ComfyUI-Prompt-Manager) and [Registry entry](https://api.comfy.org/nodes/prompt-manager).

Why use it:

- The durable library and recipe system solves a broader creative-asset problem than one enhancement node.
- It can analyze several reference images.
- It can request JSON-object output and store richer generation metadata.

Critical caution:

The inspected managed llama.cpp path calls a function that kills every process whose name includes `llama-server` before starting its own server. That is the same unsafe ownership pattern identified in this project. It can disrupt unrelated local services.

Verdict: Worth using with the Ollama backend if recipe organization is the actual need. Avoid its managed llama-server mode while unrelated llama-server processes matter.

### 11. Eric's Prompt Enhancers

Position: Diffusion and video-model-specific prompt transformation.

The package offers five prompt-enhancement nodes covering text-to-image, image-to-image, image-to-video, and video prompts. It encodes target-specific handling for Flux, SDXL, Pony, Illustrious, Chroma, Qwen Image/Edit, Wan Image, and video, with modes for expansion, refinement, style changes, reference fidelity, aesthetic controls, negative prompts, and multiple variations. It uses LM Studio, Ollama, or an optional direct Qwen path. See the [project repository](https://github.com/EricRollei/Local_LLM_Prompt_Enhancer) and [Registry entry](https://api.comfy.org/nodes/comfyui-erics-prompt-enhancers).

Why it is interesting:

- It recognizes that a good prompt for one image or video model may be wrong for another.
- It exposes intent such as preserving the subject while refining style.
- It offers more explicit prompt controls than a generic "improve this" instruction.

Why it is not a default:

- Maintenance has slowed.
- Current issues include an undefined helper in the direct Qwen path, Ollama model-selection behavior, and unclear video output.
- Requests are non-streaming.
- The normal license is CC BY-NC, and the license explicitly treats monetized content and business use as commercial use requiring a separate commercial license.

Verdict: A worthwhile personal, noncommercial experiment through LM Studio or a controlled Ollama setup. Do not build a commercial or dependable base around it without resolving licensing and current bugs.

### 12. ComfyUI LLM Party

Position: A large LLM application-building environment inside ComfyUI.

LLM Party includes local and remote model loaders, agents, personas, persistent history, tools, RAG, embeddings, vector search, knowledge graphs, MCP, workflow-as-tool execution, file and browser operations, speech and media utilities, and many supporting nodes. It is categorically broader than a prompt enhancer. See the [project repository](https://github.com/heshengtao/comfyui_LLM_party), [Registry entry](https://api.comfy.org/nodes/comfyui_llm_party), and [Registry version history](https://api.comfy.org/nodes/comfyui_llm_party/versions).

Why someone might use it:

- They intentionally want to build an LLM application, agent graph, RAG workflow, or model-as-tool system inside ComfyUI.
- Its file-backed histories are more credible than process-only chat state.
- It covers both in-process models and external local endpoints.

Why it is a poor casual temporary install:

- The dependency surface covers dozens of packages across Transformers, LangChain, vector databases, audio, video, OCR, browser tooling, embeddings, databases, TTS, and other utilities.
- Its import path can install `llama-cpp-python`, replace websocket packages, replace Discord packages, and dynamically import custom tools.
- Tool-capable workflows can access files, execute workflows, use browsers or databases, and optionally execute code. A downloaded workflow must be treated as executable behavior.
- Registry release state is confusing: current main declares 1.4.1, Registry 1.4.x is flagged, 1.3.x is banned, and the Registry selects 1.2.0 as the latest active version. The Registry API does not expose the reason, so this report does not speculate.
- Current reports include VRAM release, installation, frontend, startup, and saved-workflow compatibility problems.

Verdict: Use only when agent, RAG, memory, or tool breadth is the actual project. Do not install it into a primary ComfyUI environment merely to enhance prompts.

### 13. IF_LLM

Position: A profile-driven prompt and visual-captioning appliance.

IF_LLM is the current descendant of the old IF PromptImaGen branch, not a maintained continuation of the entire archived IF_AI_tools RAG suite. It combines local backend selection with profiles, embellishments, styles, negative prompts, wildcards, image inputs, batches, and specialized strategies such as Omost. See the [current IF_LLM project](https://github.com/if-ai/ComfyUI-IF_LLM), [archived IF_AI_tools project](https://github.com/if-ai/ComfyUI-IF_AI_tools), and [Registry entry](https://api.comfy.org/nodes/comfyui-if_llm).

Why it remains conceptually appealing:

- The main node feels like a prompt product rather than an infrastructure kit.
- It supports Ollama, llama.cpp server, LM Studio, KoboldCpp, TextGen WebUI, and local Transformers.
- Its profiles and preset libraries encode many prompt tasks.

Current problems:

- No code commit was found after 2025-04-09.
- Current source uses `loop.run_until_complete`, and an open post-update issue reports that the event loop is already running.
- Reported output can be list-valued when downstream CLIP nodes expect a string, requiring an adapter node.
- The UI mixes local and cloud providers and strategies. A user report describes unintended Gemini use while Ollama was selected, so it should not be treated as a strong offline boundary.
- Its recommended dedicated prompt fine-tune is an older Llama 3 8B model from a different model generation.

Verdict: The focused concept is good, but the current package should only be tested in a disposable ComfyUI installation. Prompt Assistant is the safer current first choice for prompt UX.

### 14. MultiModal Prompt Nodes and Simple Qwen3-VL GGUF

Position: Direct current GGUF vision models for power users.

`ComfyUI-MultiModal-Prompt-Nodes` is specialized around Qwen Image Edit and Wan workflows, including image input, several Qwen VL generations, and output conventions such as Chinese prompts where the target pipeline benefits. `ComfyUI_Simple_Qwen3-VL-gguf` exposes a broader set of current llama.cpp model families, multiple images, video frames, model-dependent audio, JSON configuration, cache modes, and explicit unload controls. See [MultiModal Prompt Nodes](https://github.com/kantan-kanto/ComfyUI-MultiModal-Prompt-Nodes) and [Simple Qwen3-VL GGUF](https://github.com/KLL535/ComfyUI_Simple_Qwen3-VL-gguf).

Why use them:

- The workflow needs current direct-GGUF VLM support.
- The user is comfortable managing custom `llama-cpp-python` wheels.
- Target-specific prompt formatting is valuable.

Why not start here:

- Several current vision integrations document a JamePeng wheel or a source build.
- Registry releases can lag later GitHub features.
- The installation and upgrade surface is technical.
- They are not persistent session systems or broad prompt libraries.

Verdict: Excellent power-user options when direct GGUF is itself a requirement. An external local runtime is easier for a temporary general solution.

## Smaller Bridges and Emerging Suites

### Generic OpenAI-compatible nodes

Projects such as [`hekmon/comfyui-openai-api`](https://github.com/hekmon/comfyui-openai-api) can point at a local Ollama, LM Studio, or llama-server OpenAI-compatible endpoint. This is useful when portability and a small node surface matter. The tradeoff is that a generic bridge usually does not manage local models, expose provider-specific unload semantics, preserve sessions, or provide prompt-assistant UX.

Verdict: A sensible minimal option for users who already manage their local server and want standard request portability.

### Ollama Prompt Encode

[`comfyui-ollama-prompt-encode`](https://github.com/ScreamingHawk/comfyui-ollama-prompt-encode) is a narrow replacement for `CLIP Text Encode (Prompt)`: it sends the user's text to Ollama, returns the generated prompt as a string, and encodes it with the connected CLIP model. It can pull a missing Ollama model and exposes seed and prepend-tag controls.

The attraction is its small mental model. The drawbacks are equally clear: it couples enhancement and encoding into one node, was last pushed in 2024, was tested against much older Ollama releases, has modest adoption, and lacks the current interaction and lifecycle features of Prompt Assistant, Deno, or `comfyui-ollama`.

Verdict: A focused legacy option for an existing workflow, not the first new install in 2026.

### llama-swap nodes

[`comfyui_llama_swap`](https://github.com/ai-joe-git/comfyui_llama_swap) provides a small client for users who already operate llama-swap, including model selection, vision, thinking extraction, and auto-unload. Its adoption and Registry signal are small, and the node is not a llama-swap installer or manager.

Verdict: Useful only when llama-swap is already the chosen local runtime.

### ComfyUI Eclipse Smart LM

[`ComfyUI_Eclipse`](https://github.com/r-vage/ComfyUI_Eclipse) is an emerging broad suite with Transformers, GGUF, Docker vLLM/SGLang/Ollama/llama.cpp, Florence2, WD14, prompt transformations, vision, OCR, and multi-stage tasks. Its current breadth is interesting, but the project is young, recently restructured, has limited independent field evidence, and carries a large surface.

Verdict: Track or trial in an isolated installation. It is too young and broad to displace the focused recommendations.

## Options I Would Not Use as the First Temporary Solution

### Ollama Describer

`ComfyUI-Ollama-Describer` has historical adoption, but its latest Registry version is marked deprecated. It should not be a new default.

### Llama Prompt Generator

`ComfyUI-Llama-Prompt-Generator` has an attractive single-node interface with live streaming, presets, refinement, version history, diffing, galleries, prefix and suffix preservation, and image analysis. However, it was created in June 2026, has almost no field history, lacks a Registry entry, has inconsistent license statements, and inherited process-wide llama-server killing from Prompt Manager.

Verdict: Watch its UX ideas. Do not use it as the dependable current choice.

### RebelsPromptEnhancer

This is a new direct Qwen prompt enhancer with interesting prompt-locking ideas, but it is explicitly a work in progress, has no Registry entry or clear license metadata, and its repository layout appears unlikely to load through the normal documented clone path.

Verdict: Not ready for a user recommendation.

### Archived IF_AI_tools

The repository is archived and directs users elsewhere. Installing it beside IF_LLM can also create naming conflicts.

Verdict: Do not install it now.

### Older broad VLM packs

Historically popular VLM packs can still appear in shared workflows, but several now carry outdated model integrations, broad dependency lists, and current reports of NumPy, Transformers, or startup dependency mutation breaking current ComfyUI.

Verdict: Prefer a current focused Qwen, Florence2, or JoyCaption implementation unless an old workflow specifically requires the legacy node.

## Ollama Versus LM Studio Versus llama-server

### Ollama

Best for:

- Simple installation and a service-oriented local model library.
- Broad support across current ComfyUI prompt and LLM plugins.
- A stable local name such as `qwen3.5:9b` shared by several clients.
- Explicit `keep_alive` behavior when the client forwards it correctly.

Tradeoffs:

- Model packaging and templates are Ollama's abstraction rather than direct raw llama.cpp control.
- The service remains a separate VRAM owner.
- Different clients expose different subsets of Ollama features.
- Ollama also offers cloud-tagged models, so strict local use requires choosing local model tags and not cloud variants.

The official library currently lists local Qwen 3.5 and Qwen3-VL sizes suitable for this machine. See [Qwen 3.5 in Ollama](https://ollama.com/library/qwen3.5) and [Qwen3-VL in Ollama](https://ollama.com/library/qwen3-vl).

### LM Studio

Best for:

- A graphical model browser and download experience.
- Users who want to inspect and manage loaded local models without a CLI-first workflow.
- OpenAI-compatible endpoints plus LM Studio's native model list, load, unload, and download APIs.
- Structured JSON Schema responses when the model and endpoint support them.
- Time-to-live and auto-evict behavior.

Tradeoffs:

- The desktop management layer is proprietary even though inference remains local.
- It is still a separate process and VRAM owner.
- Some ComfyUI plugins test Ollama more thoroughly than LM Studio.

See LM Studio's [local REST API](https://lmstudio.ai/docs/developer/rest), [local server guide](https://lmstudio.ai/docs/developer/core/server), and [TTL and auto-evict documentation](https://lmstudio.ai/docs/developer/core/ttl-and-auto-evict).

### llama-server

Best for:

- Direct llama.cpp control.
- Raw GGUF workflows, current llama.cpp features, and minimal runtime abstraction.
- Users who already understand model, projector, context, slot, cache, and server parameters.
- Future migration back to a modernized `comfyui-llamacpp`.

Tradeoffs:

- The user owns the binary, flags, model paths, and upgrades.
- Current router and endpoint contracts evolve quickly.
- A ComfyUI plugin that claims to manage it must be safe about process ownership.
- Thin generic clients often do not expose model download, router, or lifecycle features.

### llama-swap

Best for:

- Users who want llama.cpp model hot-swapping and unload policies outside ComfyUI.
- Sharing one model-routing service across applications.

Tradeoffs:

- It is another configuration layer.
- ComfyUI node support is smaller.
- It is excessive if one small model is sufficient.

## Model Choices for This Workstation

Observed local hardware:

- NVIDIA RTX 5090 with 32,607 MiB reported VRAM.
- AMD Ryzen 9 9950X3D.
- 94 GiB reported system RAM.
- No Ollama or llama-server executable and no service on the common Ollama or LM Studio ports was visible in the inspected Linux/WSL environment.

The practical implications are:

- A current 4B to 9B quantized model is easy to justify for ancillary prompt work.
- A 27B quantized model can likely fit by itself, but that does not mean it should remain loaded beside a large diffusion or video pipeline.
- Model file size is not the same as final VRAM use. Runtime buffers, KV cache, vision projection, context length, and implementation overhead add memory.
- The correct policy for a shared single GPU is usually "load for the task, then unload" unless the next graph stages are light.

Recommended starting hypotheses:

| Task | First model class | Why |
| --- | --- | --- |
| One model for text rewriting and image understanding | Qwen 3.5 9B local tag if that runtime accepts image input | A plausible broad starting point without 27B cost; otherwise pair it with Qwen3-VL |
| Fast prompt rewriting | Qwen 3.5 4B or another current 3B to 4B instruct model | Low latency and easy VRAM handoff |
| General image or short-video interrogation | Qwen3-VL 4B or 8B | Current vision instruction following |
| Lightweight visual utility | Florence2 large or a suitable PromptGen fine-tune | Fast caption, OCR, box, and mask tasks |
| Dataset captioning | JoyCaption | Task-specific caption styles and uncensored coverage |
| Anime tag upsampling | TIPO 500M | Specialist model is smaller and more aligned than a generic assistant |

This is a starting matrix, not a benchmark claim. The model should be evaluated on the user's real prompt and image set, especially for fidelity and unwanted additions.

## Local-Only Does Not Automatically Mean Offline-Safe

A local model can still be wrapped by a plugin that supports cloud providers, remote translation, model downloads, telemetry, or arbitrary endpoint URLs. For a strict local-only trial:

1. Bind the runtime to loopback unless LAN access is intentionally needed.
2. Configure only `127.0.0.1` or `localhost` endpoints.
3. Leave cloud API credentials empty.
4. Avoid cloud-tagged model names.
5. Download models first, then test with outbound networking disabled if offline behavior is important.
6. Check that translation, captioning, and enhancement each use the intended local service. Some assistants configure these independently.
7. Review downloaded workflows as executable behavior, especially when a suite exposes file, browser, SQL, code, MCP, or workflow-execution tools.
8. Store custom system prompts and presets in a portable file rather than only inside plugin-private UI state.

Local inference is a deployment choice. It is not, by itself, proof that every surrounding feature stays local.

## Practical Trial Paths

### Trial A: Best general temporary stack

Goal: One local model for prompt rewriting, image interrogation, and graph use.

1. Install Ollama or LM Studio outside ComfyUI.
2. Load a current Qwen 3.5 9B local model, and confirm the selected runtime tag accepts image input before treating it as the vision model.
3. Install Deno's node pack in a test ComfyUI environment.
4. Use Local LLM Loader with unload-after-run enabled.
5. Set ComfyUI model unloading to `Always` for this strict single-GPU test. `Auto` trusts a warm marker and provider model-list checks that can be stale or ambiguous.
6. Test text-only, one image, cancel, model unload, and repeated queues. Require both provider-state confirmation and driver-visible free-memory recovery before the next diffusion load.
7. Keep `comfyui-ollama` available as the fallback if Deno's new feature is unstable.

Why this path:

- The model runtime is reusable and isolated.
- Deno currently exposes the most deliberate VRAM handoff and request UX.
- The workflow is closer to a future external-server `comfyui-llamacpp` path than an in-process Transformers installation.

### Trial B: Best prompt-writing experience

Goal: Improve, translate, organize, and reverse-prompt while editing workflows.

1. Run Ollama locally with a 4B or 9B instruction/VLM model.
2. Install Prompt Assistant in a test or easily reversible ComfyUI installation.
3. Configure enhancement, translation, and captioning explicitly to the local service.
4. Create a small set of fidelity rules, rather than importing a giant generic prompt catalog.
5. Test both a small workflow and the user's largest normal graph for canvas performance.
6. Export or separately save important prompt rules and tags.

Why this path:

- It directly improves the user's authoring surface.
- It avoids building an enhancement subgraph into every workflow.
- The same Ollama backend remains usable by other graph nodes.

### Trial C: Best visual caption stack

Goal: Turn images or video frames into useful prompt text.

1. Start with Florence2 for fast captions, OCR, grounding, boxes, or masks.
2. Compare QwenVL 4B or 8B on a smaller representative set when custom instructions and detail matter.
3. Use JoyCaption only for rich training captions or content where its uncensored specialization matters.
4. Keep each model unloaded when moving into a large generation stage.
5. Human-review sample outputs for omitted subjects, hallucinated attributes, style bias, and inappropriate demographic inference.

Why this path:

- It uses a small specialist where possible and a general VLM only where necessary.
- It avoids treating every image-to-text job as the same task.

### Trial D: Persistent local conversation

Goal: Resume multi-turn conversations or model-to-model dialogue inside a workflow.

1. Use ComfyUI-LLM-Session in an isolated environment.
2. Begin with a text-only GGUF and the ordinary supported wheel path.
3. Add multimodal support only after choosing a wheel that matches Python, CUDA, GPU, and the specific model handler.
4. Verify transcript persistence after ComfyUI restart.
5. Test history summarization, context growth, cache behavior, and explicit unload.
6. Do not confuse a working one-turn call with a working long-lived session.

Why this path:

- It is the only reviewed option where durable session behavior is the core product rather than a side effect.

### Trial E: Anime or Danbooru prompt expansion

Goal: Generate category-aware tag prompts without loading a general LLM.

1. Try TIPO 500M.
2. Define banned tags and output category format.
3. Compare it against the original prompt, not just against another generated prompt.
4. Use a general LLM only for natural-language scene planning or tasks outside TIPO's domain.

## What to Measure During a Trial

Use the same small evaluation set across tools:

- Five short raw prompts that must preserve named subjects, counts, colors, composition, and exclusions.
- Five detailed prompts where the assistant should make minimal edits.
- Five images covering photo, illustration, text-heavy image, multiple subjects, and an unusual composition.
- One representative large ComfyUI workflow.
- One repeated queue of at least five prompt plus generation runs.

Record:

| Measure | What to look for |
| --- | --- |
| Fidelity | Did the tool preserve every requested subject, relationship, count, and constraint? |
| Additions | Did it invent wardrobe, ethnicity, mood, lighting, camera, or story elements? |
| Target fit | Does the result match the target image or video model's preferred prompt form? |
| Edit visibility | Can the user compare original and enhanced text? |
| Latency | Cold load, warm request, and total graph delay |
| VRAM | Before call, during generation, after requested unload, and before diffusion starts |
| Repeatability | Seed behavior and variation across queues |
| Workflow stability | Save, reopen, restart, missing model, wrong URL, and cancelled request |
| Local boundary | Network destinations during enhancement, translation, and captioning |
| Portability | Can the system instruction and final text survive a move to another node? |

The most important test is not whether a tool produces a longer prompt. It is whether it produces a more useful prompt without silently changing the user's intent.

## Migration Cost Back to `comfyui-llamacpp`

Not every temporary choice creates the same lock-in.

| Temporary choice | Migration cost | Reason |
| --- | --- | --- |
| Core `Generate Text` | Medium | Model object and template semantics are Comfy-native, but prompts remain strings |
| Deno Local LLM Loader with llama.cpp or OpenAI-compatible endpoint | Low to medium | External endpoint and prompt strings map naturally to a future server client |
| `comfyui-ollama` | Medium | Prompts are portable, but Ollama model names and lifecycle semantics differ |
| Prompt Assistant | Medium to high for UX state | Final prompts are portable; rules, tags, histories, and UI behavior are plugin-specific |
| LLM Session | High for sessions | Transcript, summary, and cache semantics are product-specific |
| Florence2, JoyCaption, QwenVL, TIPO | Low for text outputs, high for task behavior | Strings can feed any downstream node, but specialist modes and structured outputs cannot be reproduced by a generic client automatically |
| LLM Party or IF_LLM | High | Workflows depend on many package-specific nodes, profiles, and state conventions |

To preserve optionality:

- Keep the raw user prompt and enhanced prompt as separate graph values.
- Store system prompts as ordinary text files or workflow strings.
- Avoid depending on one plugin's hidden prompt history as the only copy.
- Preserve structured results before converting them to a final prompt string.
- Prefer an external local server when the task is generic text or vision inference.

## What These Alternatives Teach the Project

This user-focused comparison reinforces, rather than replaces, the main frontier review:

- Deno shows that explicit VRAM handoff, stop, unload, live preview, and provider-specific lifecycle behavior are now part of the expected graph experience.
- Prompt Assistant shows that many users want an editing companion, history, presets, translation, and fast actions more than a more elaborate sampling node.
- LLM Session shows that real chat history requires durable session identity, transcripts, summaries, and cache semantics. `cache_prompt` is not enough.
- Core `Generate Text` shows that `comfyui-llamacpp` should not duplicate first-party model reuse when a compatible encoder is already in the graph.
- Florence2, JoyCaption, QwenVL, and TIPO show that task-specific models should remain adjacent tools, not be absorbed into a broad all-in-one node pack.
- Prompt Manager and the new Llama Prompt Generator repeat this project's unsafe process-ownership problem, confirming that a safe managed llama-server lifecycle remains a meaningful differentiator.
- LLM Party and IF_LLM show the maintenance cost of broad dependency surfaces, mixed provider modes, and large preset catalogs.

The project still has a plausible niche: a small, dependable, transparent ComfyUI client and optional lifecycle wrapper for external llama.cpp-compatible servers. It should compete on safety, current protocol support, prompt fidelity, structured output, model routing, progress, and VRAM handoff, not on being the largest LLM suite.

## Worth Using Now

- Core `Generate Text`, when a compatible model is already loaded.
- Prompt Assistant plus local Ollama, for interactive prompt work after testing a large graph.
- `comfyui-ollama`, for a mature focused Ollama bridge.
- Florence2, for fast caption, OCR, detection, and grounding tasks.
- QwenVL, when flexible current vision instructions justify the heavier stack.
- TIPO, for anime and Danbooru tag expansion.

## Worth a Controlled Trial

- Deno Local LLM Loader, because its lifecycle controls are unusually deliberate but do not yet form a confirmed end-to-end VRAM barrier, and the feature is very new.
- ComfyUI-LLM-Session, when durable chat is required.
- JoyCaption, for rich dataset captions.
- Prompt Manager with Ollama, when recipe management is valuable.
- Eric's Prompt Enhancers, for personal noncommercial model-specific prompting.
- Simple Qwen3-VL GGUF, for users comfortable maintaining current custom wheels.
- ComfyUI Eclipse, in an isolated installation.

## Not Worth Choosing as the First Install

- LLM Party for simple prompt enhancement.
- IF_LLM in a primary current ComfyUI environment.
- Archived IF_AI_tools.
- Deprecated Ollama Describer.
- Llama Prompt Generator until lifecycle safety, packaging, license clarity, and field history improve.
- RebelsPromptEnhancer until basic packaging and maintenance signals improve.
- Older broad VLM packs that mutate or pin core dependencies on startup.
- Any managed server node that kills every `llama-server` process on the machine.

## Evidence Quality and Remaining Uncertainty

Observed facts in this guide come from current repositories, source inspection, Comfy Registry metadata, official runtime documentation, local hardware inspection, and current GitHub issues. Community reports from Reddit and issue trackers were used to identify recurring user pain and workflow patterns, not as proof that every user will reproduce a problem.

Unverified locally:

- No candidate plugin was installed into this workstation's primary ComfyUI environment.
- No model quality, latency, or VRAM benchmark was run.
- Prompt Assistant canvas performance was not tested against the user's own large workflows.
- Deno Local LLM Loader is too new for long field history.
- The best Qwen quantization and context settings for this RTX 5090 remain empirical.
- WSL versus Windows-hosted runtime placement may change endpoint and GPU behavior.

These are appropriate next checks only if the user chooses a trial. They are not reasons to install several candidates at once.

## Primary Sources

ComfyUI and runtimes:

- `https://github.com/Comfy-Org/ComfyUI/blob/master/comfy_extras/nodes_textgen.py`
- `https://docs.comfy.org/built-in-nodes/TextGenerate`
- `https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md`
- `https://ollama.com/library/qwen3.5`
- `https://ollama.com/library/qwen3-vl`
- `https://lmstudio.ai/docs/developer/rest`
- `https://lmstudio.ai/docs/developer/core/server`
- `https://lmstudio.ai/docs/developer/core/ttl-and-auto-evict`

General bridges, assistants, and sessions:

- `https://github.com/Deno2026/comfyui-deno-custom-nodes`
- `https://api.comfy.org/nodes/deno-custom-nodes`
- `https://github.com/stavsap/comfyui-ollama`
- `https://api.comfy.org/nodes/comfyui-ollama`
- `https://github.com/ScreamingHawk/comfyui-ollama-prompt-encode`
- `https://github.com/hekmon/comfyui-openai-api`
- `https://github.com/ai-joe-git/comfyui_llama_swap`
- `https://github.com/yawiii/ComfyUI-Prompt-Assistant`
- `https://api.comfy.org/nodes/prompt-assistant`
- `https://github.com/kantan-kanto/ComfyUI-LLM-Session`
- `https://api.comfy.org/nodes/comfyui-llm-session`
- `https://github.com/FranckyB/ComfyUI-Prompt-Manager`
- `https://api.comfy.org/nodes/prompt-manager`
- `https://github.com/EricRollei/Local_LLM_Prompt_Enhancer`
- `https://api.comfy.org/nodes/comfyui-erics-prompt-enhancers`

Suites and specialist nodes:

- `https://github.com/heshengtao/comfyui_LLM_party`
- `https://api.comfy.org/nodes/comfyui_llm_party/versions`
- `https://github.com/if-ai/ComfyUI-IF_LLM`
- `https://github.com/if-ai/ComfyUI-IF_AI_tools`
- `https://github.com/1038lab/ComfyUI-QwenVL`
- `https://api.comfy.org/nodes/ComfyUI-QwenVL`
- `https://github.com/kijai/ComfyUI-Florence2`
- `https://api.comfy.org/nodes/comfyui-florence2`
- `https://github.com/fpgaminer/joycaption`
- `https://github.com/1038lab/ComfyUI-JoyCaption`
- `https://github.com/KohakuBlueleaf/z-tipo-extension`
- `https://api.comfy.org/nodes/z-tipo-extension`
- `https://github.com/KLL535/ComfyUI_Simple_Qwen3-VL-gguf`
- `https://github.com/kantan-kanto/ComfyUI-MultiModal-Prompt-Nodes`
- `https://github.com/r-vage/ComfyUI_Eclipse`

Representative community evidence:

- `https://www.reddit.com/r/comfyui/comments/1l1jg2v/running_llm_models_in_comfyui/`
- `https://www.reddit.com/r/comfyui/comments/1r5ik1p/anyone_having_much_luck_with_incorporating_local/`
- `https://www.reddit.com/r/comfyui/comments/1pkrp5d/i_fell_in_love_with_qwen_vl_for_captioning_but_it/`
- `https://www.reddit.com/r/comfyui/comments/1uh6034/suggestions_for_good_prompt_enhancer/`
- `https://www.reddit.com/r/StableDiffusion/comments/1o1gbye/is_joycaption_still_the_best_tagging_model/`
- `https://www.reddit.com/r/comfyui/comments/1k08j57/lightweight_image_captioners_better_than_florence/`
- `https://www.reddit.com/r/StableDiffusion/comments/1ul5dcv/introducing_local_llm_loader_a_node_that_makes/`

## Bottom Line

The most useful local-only temporary solution is not "the biggest LLM node pack." It is a small stack chosen around the user's real job:

- Reuse core `Generate Text` when the model is already in the workflow.
- Use an external local runtime for generic inference isolation.
- Use Deno or `comfyui-ollama` for graph calls.
- Use Prompt Assistant for the writing experience.
- Use LLM Session only when durable conversation matters.
- Use Florence2, QwenVL, JoyCaption, or TIPO for the specialist jobs they are actually good at.

For this machine, the best first experiment is a local Qwen 3.5 9B model under Ollama or LM Studio, accessed through Deno Local LLM Loader or Prompt Assistant depending on whether graph control or interactive writing matters more. Confirm image support for the exact model tag and runtime; if it is not available, use Qwen3-VL 4B or 8B for vision. The main risk is not insufficient compute. It is installing too many overlapping ComfyUI model stacks and creating dependency, VRAM, and workflow-maintenance problems before the user has identified which experience they actually want.
