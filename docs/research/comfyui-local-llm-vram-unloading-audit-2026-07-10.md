# ComfyUI Local LLM and VLM VRAM Unloading Audit

Date: 2026-07-10
Mode: Research and static analysis only
Product implementation: None

## Question

Does the historical failure mode where a local LLM keeps GPU memory until ComfyUI is restarted still apply, or can current ComfyUI and current local LLM nodes unload models as reliably as ComfyUI unloads diffusion models?

## Short Answer

The old statement is now too broad.

Current ComfyUI has a proper model-management path, and its first-party `Generate Text` node uses that path. Supported native generative text and vision models can therefore be pressure-evicted, offloaded, and manually unloaded through the same `ModelPatcher` machinery used for other Comfy-managed models. A ComfyUI restart should not normally be required for those models.

That behavior is not automatic for arbitrary custom-node objects. A raw Transformers model, an in-process `llama_cpp.Llama`, a module-global cache, or an external Ollama, LM Studio, vLLM, or llama-server process does not become Comfy-managed merely because a node uses it. Cleanup remains the responsibility of the node pack or external runtime.

The accurate conclusion is:

> ComfyUI now has proper native LLM unloading, but not a universal LLM unloading system. The original external-process rationale for comfyui-llamacpp remains valid for broad GGUF use, native llama.cpp allocations, dependency isolation, and deterministic teardown. It is no longer unique across every LLM solution, and our current router and handoff implementation is not yet a strict VRAM barrier.

## Evidence Baseline

The ComfyUI architecture was checked against both:

- Latest stable at retrieval: `v0.27.1`, commit `c2638ce6c00e3426c48d56a775bc46e9a8464094`, released 2026-07-08.
- Current master at retrieval: `1377a2f72925ed7a5518c1900ff71c6740217b0d`, dated 2026-07-10.

The relevant lifecycle behavior is materially the same in both revisions. The audit also inspected current revisions of ComfyUI frontend, llama.cpp, llama-cpp-python, Ollama, llama-swap, LLM Party, LLM Session, QwenVL, Florence2, JoyCaption, comfyui-ollama, Deno Local LLM Loader, Prompt Assistant, and Simple Qwen3-VL GGUF. Reference checkouts were treated as untrusted and were not imported or executed.

No model or GPU benchmark was run. Runtime statements below are separated from source-backed architecture and user reports.

## What Changed in ComfyUI

### Native text generation is now a managed Comfy model

First-party text generation landed in ComfyUI `v0.15.0` in February 2026, initially for Gemma 3. Support has since expanded to additional generative model families, including Qwen3.5, Gemma 4, and Qwen3-VL.

The important part is not the node UI. It is the call chain:

```text
Generate Text
  -> CLIP.generate()
  -> CLIP.load_model()
  -> model_management.load_models_gpu([self.patcher])
  -> free_memory() and ModelPatcher offload as needed
```

The current node accepts text plus optional image, video, and audio inputs. It receives a normal Comfy `CLIP` object, and that object wraps the underlying model in `CoreModelPatcher` with explicit load and offload devices. This is real integration with Comfy's memory system, not a raw Hugging Face object moved to CUDA by a plugin.

Core generation creates its KV cache inside the `generate()` call and returns generated token IDs rather than a persistent conversation cache. The temporary KV tensors can therefore become unreachable after the call. This differs from session-oriented plugins that deliberately preserve KV state across executions.

Primary evidence:

- [ComfyUI v0.15.0](https://github.com/Comfy-Org/ComfyUI/releases/tag/v0.15.0)
- [Initial native text-generation PR](https://github.com/Comfy-Org/ComfyUI/pull/12392)
- [`TextGenerate` at stable v0.27.1](https://github.com/Comfy-Org/ComfyUI/blob/c2638ce6c00e3426c48d56a775bc46e9a8464094/comfy_extras/nodes_textgen.py#L28-L78)
- [`CLIP` patcher construction](https://github.com/Comfy-Org/ComfyUI/blob/c2638ce6c00e3426c48d56a775bc46e9a8464094/comfy/sd.py#L224-L260)
- [`CLIP.load_model()` and `generate()`](https://github.com/Comfy-Org/ComfyUI/blob/c2638ce6c00e3426c48d56a775bc46e9a8464094/comfy/sd.py#L447-L466)
- [Core text-generation KV cache lifetime](https://github.com/Comfy-Org/ComfyUI/blob/c2638ce6c00e3426c48d56a775bc46e9a8464094/comfy/text_encoders/llama.py#L871-L930)

### What the managed path actually does

ComfyUI maintains `current_loaded_models`, a registry of `LoadedModel` wrappers around `ModelPatcher` objects. When memory is needed, `free_memory()` considers only this registry. It can partially offload a managed model or fully detach it to its offload device, normally CPU. `soft_empty_cache()` then synchronizes and releases unused allocator blocks.

This is substantially the same lifecycle used by diffusion, VAE, text-encoder, and other properly integrated Comfy models.

Primary evidence:

- [Managed model registry and unload](https://github.com/Comfy-Org/ComfyUI/blob/1377a2f72925ed7a5518c1900ff71c6740217b0d/comfy/model_management.py#L610-L768)
- [Pressure-based `free_memory()`](https://github.com/Comfy-Org/ComfyUI/blob/1377a2f72925ed7a5518c1900ff71c6740217b0d/comfy/model_management.py#L816-L960)
- [`soft_empty_cache()` and `unload_all_models()`](https://github.com/Comfy-Org/ComfyUI/blob/1377a2f72925ed7a5518c1900ff71c6740217b0d/comfy/model_management.py#L1961-L1981)
- [Current ModelPatcher detach behavior](https://github.com/Comfy-Org/ComfyUI/blob/1377a2f72925ed7a5518c1900ff71c6740217b0d/comfy/model_patcher.py#L1242-L1249)

### Managed does not mean immediately cold after every prompt

Default smart-memory behavior may keep a recently used managed model resident while there is no pressure. That is an intentional cache, not necessarily a leak. The model is expected to yield when another Comfy-managed model needs memory.

Current controls include:

- `--disable-smart-memory`, which unloads all managed models after a completed prompt.
- `--cache-none`, which avoids retaining node results and node instances in the normal execution cache.
- `Unload Models`, which invokes the managed-model unload path.
- `Unload Models and Execution Cache`, which also discards cached node objects and outputs before garbage collection and allocator cleanup.

There is currently no general per-node TTL for native `TextGenerate`. An open feature request asks for one, but describes retained RAM rather than proving unreclaimable VRAM.

Sources:

- [ComfyUI CLI memory and cache flags](https://github.com/Comfy-Org/ComfyUI/blob/1377a2f72925ed7a5518c1900ff71c6740217b0d/comfy/cli_args.py#L114-L161)
- [Prompt-end aggressive unload](https://github.com/Comfy-Org/ComfyUI/blob/1377a2f72925ed7a5518c1900ff71c6740217b0d/execution.py#L827-L833)
- [TextGenerate keep-alive request](https://github.com/Comfy-Org/ComfyUI/issues/13456)

## What the Two Manual Unload Commands Mean

Current ComfyUI frontend exposes two distinct commands.

### `Unload Models`

The frontend posts:

```json
{"unload_models": true}
```

The backend calls `unload_all_models()`. This affects models registered in `current_loaded_models`. It does not search every Python object, close every third-party backend, clear module globals, or stop external processes.

### `Unload Models and Execution Cache`

The frontend posts:

```json
{"unload_models": true, "free_memory": true}
```

The backend additionally resets the `PromptExecutor` cache. Current ComfyUI normally caches node instances by node ID and also caches outputs. Resetting the executor drops those references, then the main loop runs `gc.collect()` and `soft_empty_cache()`.

This stronger command can incidentally release an unmanaged custom model if its only remaining reference is a cached node instance or cached output. It still cannot release:

- A model stored in a module global.
- A library-level registry or persistent cache.
- A native context whose owner remains reachable.
- A background thread or service retaining the model.
- An external process.
- A plugin-specific resource that requires an explicit `close()` and has no reliable destructor.

ComfyUI does not invoke a general plugin teardown callback for each cached node object during this operation.

Primary evidence:

- [Frontend unload commands](https://github.com/Comfy-Org/ComfyUI_frontend/blob/ceb5ae1eba1c4fc448c79e4ae8c67a81db42d648/src/composables/useCoreCommands.ts#L1247-L1283)
- [Frontend `/free` payloads](https://github.com/Comfy-Org/ComfyUI_frontend/blob/ceb5ae1eba1c4fc448c79e4ae8c67a81db42d648/src/scripts/api.ts#L1461-L1492)
- [Backend `/free` route](https://github.com/Comfy-Org/ComfyUI/blob/1377a2f72925ed7a5518c1900ff71c6740217b0d/server.py#L1182-L1191)
- [Node-object caching](https://github.com/Comfy-Org/ComfyUI/blob/1377a2f72925ed7a5518c1900ff71c6740217b0d/execution.py#L496-L499)
- [Executor reset](https://github.com/Comfy-Org/ComfyUI/blob/1377a2f72925ed7a5518c1900ff71c6740217b0d/execution.py#L662-L672)
- [Free flags, GC, and cache cleanup](https://github.com/Comfy-Org/ComfyUI/blob/1377a2f72925ed7a5518c1900ff71c6740217b0d/main.py#L387-L405)

## Why `nvidia-smi` Alone Can Mislead

Several different states are commonly described as "VRAM not released."

| State | What still owns memory | Does `torch.cuda.empty_cache()` solve it? | Correct response |
| --- | --- | --- | --- |
| Live model or tensor | Python object or native owner | No | Remove every live reference or offload/close the owner |
| Intentionally warm managed model | Comfy ModelPatcher registry | No, because allocations are active | Let pressure evict it or use Comfy's unload command |
| Dead tensors, reserved Torch blocks | PyTorch caching allocator | Yes, for fully unused blocks | `soft_empty_cache()` or `torch.cuda.empty_cache()` |
| Live llama.cpp allocation | GGML/llama.cpp native CUDA allocator | No | Call backend close correctly or terminate its process |
| External service model | Ollama, LM Studio, vLLM, llama-server, or another process | No | Use its native unload/sleep API or stop the process |
| CUDA context baseline | Active process and CUDA libraries | No | Usually accept it; only safe context teardown or process exit removes it |

PyTorch explicitly states that `empty_cache()` releases only unoccupied cached memory. It cannot free live tensors and does not increase the memory available to PyTorch itself. PyTorch also distinguishes active tensor allocation from allocator-reserved memory, while `nvidia-smi` attributes both to the process.

- [PyTorch `empty_cache()`](https://docs.pytorch.org/docs/main/generated/torch.cuda.memory.empty_cache.html)
- [PyTorch CUDA memory semantics](https://docs.pytorch.org/docs/main/notes/cuda.html#memory-management)

A running ComfyUI process may retain a comparatively small CUDA-context baseline even after every model is gone. NVIDIA documents that CUDA contexts consume device resources and remain active until released or reset. Calling `cudaDeviceReset()` inside a plugin host is generally unsafe because other libraries in that process share the context. This is one structural advantage of an external inference process: terminating it removes its entire context without resetting ComfyUI's context.

- [NVIDIA primary-context lifecycle](https://docs.nvidia.com/cuda/archive/12.3.1/cuda-driver-api/group__CUDA__PRIMARY__CTX.html)
- [NVIDIA guidance on context overhead](https://docs.nvidia.com/cuda/cuda-c-best-practices-guide/index.html)

## Current In-Process Node Findings

### Summary matrix

| Solution and path | Comfy ModelPatcher? | Normal retention | Teardown mechanism | Assessment |
| --- | ---: | --- | --- | --- |
| Core `Generate Text` | Yes | Smart-memory cache | Comfy pressure eviction, manual unload, or aggressive prompt-end unload | Proper Comfy lifecycle |
| Florence2 1.1.0 | Yes | `keep_model_loaded` defaults false | Targeted Comfy `free_memory()` plus `soft_empty_cache()` | Best reviewed third-party example |
| QwenVL Transformers | No | Keep-loaded defaults true | On opt-out, move model to CPU, clear refs, GC, empty cache | Meaningful custom cleanup, not core-managed |
| QwenVL GGUF | No | Keep-loaded defaults true | Clear refs, GC, empty cache; no explicit `Llama.close()` | Backend-version dependent |
| LLM Session 1.3.0 | No | Session model remains in a module-global runtime container | Signature change, dedicated unload node, or process exit clears managers, cache state, refs, and GC | Deliberate and probably workable, not core-managed |
| JoyCaption HF | No | Node-instance predictor; Keep in Memory defaults on | Clear-after-run drops the node predictor | Custom cleanup; advertised global cache does not populate in the reviewed path |
| JoyCaption GGUF | No | Node-instance predictor, or explicit module-global cache | Clear-after-run drops node ref; global-cache mode persists; no explicit full close | Custom and cache-mode dependent |
| LLM Party direct models | No | Loader/node dependent | Python referrer mutation, GC, empty cache; no explicit GGUF close | Unreliable by source and open reports |
| Simple Qwen3-VL GGUF | No | Subprocess is current default | Default process exit; optional direct cleanup or retained caches | Strong current GGUF choice for teardown |

### Florence2 demonstrates that custom nodes can integrate correctly

Current Florence2 wraps its model in `comfy.model_patcher.ModelPatcher`, loads through Comfy's manager, and uses `free_memory()` plus `soft_empty_cache()` when `keep_model_loaded` is false. That option defaults to false.

This is the closest reviewed custom VLM pack to the lifecycle of native diffusion models. It proves that the architecture is available to third-party authors when their model is compatible with the patcher contract.

- [Florence2 patcher construction](https://github.com/kijai/ComfyUI-Florence2/blob/9ece3de914214c5f581d725167bc9d0eeb0d1120/nodes.py#L56-L99)
- [Florence2 managed load and unload](https://github.com/kijai/ComfyUI-Florence2/blob/9ece3de914214c5f581d725167bc9d0eeb0d1120/nodes.py#L718-L723)

### LLM Session has a real explicit unload, but it is self-managed

ComfyUI-LLM-Session 1.3.0 holds in-process `llama_cpp.Llama` managers in its own runtime container. Its dedicated unload operation clears cache and manager state, deletes model and chat-handler references, runs garbage collection, and exposes an `Unload LLM Model` output node.

The model manager itself remains reachable through a module-global runtime container after ordinary generation. A matching model signature reuses the same model, and a signature change unloads it before replacement. The `runtime_cache` setting controls KV, RAM-trie, or disk-backed inference state; it does not control base-model residency. Base-model release therefore requires a signature change, the dedicated unload node, dialogue-manager cleanup, or process exit.

That is much more deliberate than relying on Comfy's ordinary unload button. It still does not call `Llama.close()` explicitly. Its real behavior therefore depends on every reference being cleared and on the installed llama-cpp-python destructor working correctly. Text-only teardown is the stronger case; multimodal teardown also depends on the installed chat handler releasing its own native context. The included tests validate control flow with dummy objects rather than measuring live CUDA recovery.

- [LLM Session unload implementation](https://github.com/kantan-kanto/ComfyUI-LLM-Session/blob/b158e776377f9b52c2fe3e449dc8d3c44cdd23c3/llm_session_nodes.py#L2704-L2747)
- [Runtime-manager cleanup](https://github.com/kantan-kanto/ComfyUI-LLM-Session/blob/b158e776377f9b52c2fe3e449dc8d3c44cdd23c3/llm_session_nodes.py#L2780-L2811)
- [Unload node and process-exit cleanup](https://github.com/kantan-kanto/ComfyUI-LLM-Session/blob/b158e776377f9b52c2fe3e449dc8d3c44cdd23c3/llm_session_nodes.py#L4605-L4670)
- [Original unload request and user confirmation](https://github.com/kantan-kanto/ComfyUI-LLM-Session/issues/3)

### QwenVL has two materially different paths

The Transformers implementation keeps the model in instance fields. When `keep_model_loaded` is false, it moves the model to CPU, clears model, processor, and tokenizer references, performs garbage collection, and empties the Torch cache. This is a reasonable self-managed PyTorch teardown, although the default is to keep the model loaded and Comfy's manager cannot pressure-evict it.

The GGUF implementation clears its raw llama-cpp model and handler references, runs GC, and calls `torch.cuda.empty_cache()`. It does not explicitly call `Llama.close()`, and `empty_cache()` has no effect on live llama.cpp native allocations. The pack recommends the JamePeng llama-cpp-python fork for vision but does not pin a version. Historical issues report retention and confirm that a one-shot subprocess workaround avoided it.

- [QwenVL Transformers cleanup](https://github.com/1038lab/ComfyUI-QwenVL/blob/fcd1ada87a28f922cb887f779db32429f78a022c/AILab_QwenVL.py#L539-L567)
- [Transformers keep-loaded default and final cleanup](https://github.com/1038lab/ComfyUI-QwenVL/blob/fcd1ada87a28f922cb887f779db32429f78a022c/AILab_QwenVL.py#L817-L857)
- [QwenVL GGUF cleanup](https://github.com/1038lab/ComfyUI-QwenVL/blob/fcd1ada87a28f922cb887f779db32429f78a022c/AILab_QwenVL_GGUF.py#L304-L316)
- [VRAM retention and subprocess workaround](https://github.com/1038lab/ComfyUI-QwenVL/issues/104)
- [Later retention report](https://github.com/1038lab/ComfyUI-QwenVL/issues/150)

### JoyCaption's HF and GGUF paths have different retention behavior

Both paths offer Keep in Memory, Clear After Run, and Global Cache modes, with Keep in Memory as the default. Their implementations are not equivalent.

The HF node retains its predictor on the node instance. Clear After Run deletes that reference and runs allocator cleanup. Although the UI advertises Global Cache, the reviewed callers pass the quantization value into `JC_Models` as its `memory_mode`; the cache insertion inside `JC_Models` requires that same argument to equal `Global Cache`. The current HF call path therefore appears not to populate `_MODEL_CACHE`. Its retention is node-instance based, so resetting Comfy's execution cache can matter if no other references survive.

The GGUF implementation explicitly inserts predictors into the module-global `_MODEL_CACHE` when Global Cache is selected. Resetting Comfy's execution cache cannot clear that dictionary. Its Clear After Run path deletes the node reference and relies on object destruction rather than explicitly closing the complete llama model and multimodal handler.

- [JoyCaption global cache](https://github.com/1038lab/ComfyUI-JoyCaption/blob/a0e9f0a17a5deb933fef341e2c7b0131e4f83c8a/JC.py#L79-L99)
- [JoyCaption cache modes and cleanup](https://github.com/1038lab/ComfyUI-JoyCaption/blob/a0e9f0a17a5deb933fef341e2c7b0131e4f83c8a/JC.py#L260-L330)
- [JoyCaption GGUF loader and cleanup](https://github.com/1038lab/ComfyUI-JoyCaption/blob/a0e9f0a17a5deb933fef341e2c7b0131e4f83c8a/JC_GGUF.py#L232-L280)

### LLM Party remains one of the weak cases

Party's direct Transformers and GGUF loaders do not use `ModelPatcher`. Its Clear Model node walks `gc.get_referrers()`, edits dictionaries and object attributes that refer to the model, runs GC, and calls `torch.cuda.empty_cache()`. It does not call `Llama.close()`.

That is aggressive but fragile reference surgery. Open issue 206 reports GGUF VRAM reaching 98 percent after three queues, with Comfy's unload controls ineffective until restart. Issue 245, opened in June 2026, is another unresolved memory-release report. These reports are user anecdotes, not a controlled benchmark, but they align with the source-level ownership problem.

- [Party Clear Model source](https://github.com/heshengtao/comfyui_LLM_party/blob/39bca5e48505d3cfc292392479c065aa58cc2a48/tools/clear_model.py#L47-L109)
- [Party raw GGUF loaders](https://github.com/heshengtao/comfyui_LLM_party/blob/39bca5e48505d3cfc292392479c065aa58cc2a48/custom_tool/llavaloader.py#L138-L214)
- [Party issue 206](https://github.com/heshengtao/comfyui_LLM_party/issues/206)
- [Party issue 245](https://github.com/heshengtao/comfyui_LLM_party/issues/245)

### A current GGUF pack has independently chosen subprocess isolation

`ComfyUI_Simple_Qwen3-VL-gguf` now defaults to a `subprocess` mode. Each inference runs in a separate Python process, and process completion ends its CUDA ownership. It also offers `direct_clean`, `keep_vram`, and named retained-cache modes plus a manual unload node.

The direct cleanup path reaches into the private `Llama._ctx`, closes only that context, deletes the object, and optionally collects garbage. It does not call the public `Llama.close()` contract or explicitly close a multimodal handler, so it should not be treated as complete native teardown. The default subprocess path is stronger because it does not depend on Python reference topology or a library destructor. This pack is important evidence that the original comfyui-llamacpp design concern remains current even though other authors have now adopted the same boundary.

- [Simple Qwen execution modes](https://github.com/KLL535/ComfyUI_Simple_Qwen3-VL-gguf/blob/2a431790796e829551ec889861718227a11e6c80/README.md#L308-L328)
- [Default subprocess and direct cleanup selection](https://github.com/KLL535/ComfyUI_Simple_Qwen3-VL-gguf/blob/2a431790796e829551ec889861718227a11e6c80/qwen3vl_node.py#L540-L567)
- [Direct model cleanup](https://github.com/KLL535/ComfyUI_Simple_Qwen3-VL-gguf/blob/2a431790796e829551ec889861718227a11e6c80/qwen3vl_run.py#L870-L895)

## llama-cpp-python Is Better Than It Was

The native backend situation has improved materially.

Current upstream llama-cpp-python 0.3.33 exposes `Llama.close()`, which closes an `ExitStack` containing the model and context. Its internal model close calls `llama_model_free()`, and its context close calls `llama_free()`. `Llama.__del__()` calls `close()` as a fallback.

- [Current `Llama.close()`](https://github.com/abetlen/llama-cpp-python/blob/e894f0d6010be8de14400359c10c87c16ddb3829/llama_cpp/llama.py#L2280-L2285)
- [Native model free](https://github.com/abetlen/llama-cpp-python/blob/e894f0d6010be8de14400359c10c87c16ddb3829/llama_cpp/_internals.py#L72-L89)
- [Native context free](https://github.com/abetlen/llama-cpp-python/blob/e894f0d6010be8de14400359c10c87c16ddb3829/llama_cpp/_internals.py#L272-L284)
- [Maintainer's completed unload issue](https://github.com/abetlen/llama-cpp-python/issues/302)

This means "in-process llama.cpp cannot unload" is no longer technically correct for a text-only `Llama` object. A pack can release that object if it explicitly closes the complete backend object and removes every reference.

There are still practical caveats:

- Many node packs call only `del`, `None`, GC, and Torch cache cleanup instead of `Llama.close()`.
- A cached node, output, module global, session manager, or circular reference can delay destruction.
- Multimodal chat handlers can own a second native context. In the inspected upstream 0.3.33 source, those handlers create an `ExitStack` with an `mtmd_free` callback but do not expose a corresponding `close()` or `__del__()` in the reviewed classes. Python's `ExitStack` documentation states that callbacks are not invoked implicitly when the stack is garbage-collected. The clean upstream close contract therefore covers the text model and context, not necessarily the handler-owned multimodal context. Reliable multimodal teardown currently needs a patched handler API, deliberate closure of the handler's private stack, or process exit. See the [upstream handler allocation](https://github.com/abetlen/llama-cpp-python/blob/e894f0d6010be8de14400359c10c87c16ddb3829/llama_cpp/llama_chat_format.py#L2776-L2828) and [Python `ExitStack` contract](https://docs.python.org/3/library/contextlib.html#contextlib.ExitStack).
- The JamePeng fork added additional explicit multimodal cleanup and reported several leak fixes in its 0.3.27 series, but packs may install older or different wheels. See its [0.3.27 lifecycle changelog](https://github.com/JamePeng/llama-cpp-python/blob/7f59a8611d76b4e202750355df7d01f3270a15da/CHANGELOG.md#L1078-L1115) and [current multimodal close method](https://github.com/JamePeng/llama-cpp-python/blob/7f59a8611d76b4e202750355df7d01f3270a15da/llama_cpp/llama_multimodal.py#L248-L264).

Explicit close is a stronger contract than hoping that garbage collection happens before the next large allocation. Process termination remains stronger than both.

## External Runtime Findings

External runtimes solve Python dependency and native-lifetime isolation, but ComfyUI does not automatically manage their VRAM. A bridge must coordinate both owners.

### External matrix

| Runtime or bridge | Frees Comfy models before LLM? | LLM-side control | Confirmed completion barrier? | Assessment |
| --- | ---: | --- | ---: | --- |
| `comfyui-ollama` | No | Workflow `keep_alive`, including zero | No | Established and usable, but not strict two-sided handoff |
| Prompt Assistant with Ollama | No | Auto-unload sends `keep_alive: 0`, default enabled | No | Good assistant UX; Ollama-side only |
| Deno with Ollama | Yes with `Always`; `Auto` is conditional | `keep_alive: 0` | No provider polling | Best reviewed ready-made two-sided attempt |
| Deno with LM Studio | Yes with `Always`; `Auto` is conditional | Native `/api/v1/models/unload` | Not verified | Correct API, but automatic errors are swallowed |
| Deno with llama.cpp router | Yes with `Always`; default `Auto` is unsafe | `/models/unload` | No | Needs status polling |
| Deno with standalone llama-server | Yes with `Always` | No router endpoint; idle sleep or process stop needed | No immediate bridge control | Server configuration dependent |
| Deno with vLLM | Yes with `Always` | `/sleep?level=1` | Not universal: V0 frontend multiprocessing may return early | Requires dev and sleep modes plus completion polling |
| Generic OpenAI-compatible node | No | No standard unload API | No | Cannot implement strict handoff by protocol alone |
| ai-joe llama-swap node | No | Calls llama-swap unload-all | Node timeout and error swallowing weaken it | Strong runtime, incomplete bridge |
| llama-swap runtime | No knowledge of Comfy | Owns and stops model processes, supports TTL | Yes for process stop | Strongest reviewed external runtime boundary |

### Ollama has a real unload request, but API success is not the final VRAM barrier

Ollama officially supports `keep_alive: 0`, `ollama stop`, and a default five-minute retention period. `comfyui-ollama` exposes the setting and Prompt Assistant defaults its Ollama auto-unload option to enabled.

Current Ollama source shows a subtle timing issue. The request handler calls `expireRunner()` and immediately returns a response whose reason is `unload`. The scheduler then starts a VRAM-recovery watcher, terminates and removes the runner from its loaded map, and waits for the recovery watcher afterward. Therefore a successful HTTP response means the unload was scheduled, not that a following GPU-heavy process can allocate the released memory at that exact instant. Even disappearance from `/api/ps` is not a complete barrier, because the loaded-map removal can precede the scheduler's VRAM-convergence wait.

Neither reviewed Comfy bridge waits for both `/api/ps` disappearance and driver-visible free-memory recovery.

- [Ollama unload documentation](https://github.com/ollama/ollama/blob/main/docs/api.md#unload-a-model)
- [Ollama keep-alive FAQ](https://github.com/ollama/ollama/blob/main/docs/faq.mdx#how-do-i-keep-a-model-loaded-in-memory-or-make-it-unload-immediately)
- [Current unload response path](https://github.com/ollama/ollama/blob/82f905cd9c06c6f0254d74c5326aa2a7f2f07e1f/server/routes.go#L412-L423)
- [Current background unload and VRAM recovery](https://github.com/ollama/ollama/blob/82f905cd9c06c6f0254d74c5326aa2a7f2f07e1f/server/sched.go#L420-L472)
- [`comfyui-ollama` keep-alive control](https://github.com/stavsap/comfyui-ollama/blob/6db7560576e5a59488708e6be13e07b5aba2432a/CompfyuiOllama.py#L203-L236)
- [Prompt Assistant's native Ollama unload](https://github.com/yawiii/ComfyUI-Prompt-Assistant/blob/e0587e6d50939ae05e92922f1ed2fce008c7f622/services/openai_base.py#L476-L516)

### LM Studio now has proper native model management

LM Studio 0.4 exposes `POST /api/v1/models/unload`. It also supports JIT loading, idle TTL, and auto-eviction. At retrieval, JIT-loaded models default to a 60-minute TTL, and auto-evict normally limits JIT retention to the latest model. Models loaded manually or through `lms load` do not necessarily inherit that TTL.

This is a major improvement over old generic OpenAI-compatible use. A generic `/v1/chat/completions` bridge still does not gain unload behavior automatically. It must call the native endpoint or configure TTL.

- [LM Studio native unload](https://lmstudio.ai/docs/developer/rest/unload)
- [LM Studio idle TTL and auto-evict](https://lmstudio.ai/docs/developer/core/ttl-and-auto-evict)
- [LM Studio server settings](https://lmstudio.ai/docs/developer/core/server/settings)

Deno uses the correct native unload endpoint and resolves the loaded instance ID. Its automatic post-run path catches and discards unload errors, and its `_lm_ttl()` helper is currently unused. The manual unload button does propagate errors.

- [Deno LM Studio unload](https://github.com/Deno2026/comfyui-deno-custom-nodes/blob/0ec785a685a797f871e3606cfb7e63cc4380c1ef/deno_local_llm_refiner.py#L1882-L1905)
- [Deno automatic unload error handling](https://github.com/Deno2026/comfyui-deno-custom-nodes/blob/0ec785a685a797f871e3606cfb7e63cc4380c1ef/deno_local_llm_refiner.py#L2772-L2797)

### Current llama-server has better unload tools, but router unload is asynchronous

Current llama.cpp provides:

- Router mode with per-model child processes.
- `POST /models/load` and `POST /models/unload`.
- `--models-max` for residency limits.
- `--sleep-idle-seconds` for automatic idle sleep in both single-model and router modes.

Idle sleep unloads model weights and KV cache and reloads them on the next task. Router unload requests a child-process stop.

The current router endpoint returns after calling `server_models::unload()`. That method marks a running model for stopping and signals its management thread; it does not wait for the child status to become unloaded. A strict client should poll `GET /models` until the selected model reports `status.value == "unloaded"`, or use the server's status event stream.

- [Current llama-server router documentation](https://github.com/ggml-org/llama.cpp/blob/07d937828636e305bc0cfe738b288f9ab05ff748/tools/server/README.md#using-multiple-models)
- [Current unload endpoint documentation](https://github.com/ggml-org/llama.cpp/blob/07d937828636e305bc0cfe738b288f9ab05ff748/tools/server/README.md#post-modelsunload-unload-a-model)
- [Current idle-sleep documentation](https://github.com/ggml-org/llama.cpp/blob/07d937828636e305bc0cfe738b288f9ab05ff748/tools/server/README.md#sleeping-on-idle)
- [Current asynchronous router unload](https://github.com/ggml-org/llama.cpp/blob/07d937828636e305bc0cfe738b288f9ab05ff748/tools/server/server-models.cpp#L981-L1006)

### vLLM can sleep, but that is not process teardown

Current vLLM sleep mode can offload model weights and discard the KV cache. Level 2 can discard both weights and KV cache. Online endpoints require development mode and `--enable-sleep-mode`.

This is a real VRAM-release mechanism, but the vLLM process and CUDA communication/context allocations remain. Its HTTP completion semantics also depend on the engine path: the current endpoint awaits the engine client, but its own source warns that with the V0 frontend-multiprocessing path the command may not have finished when the response is returned. The same router exposes `/is_sleeping`; Deno does not poll it or driver-visible memory. V1 can await the sleep operation, while a portable client must not treat every HTTP 200 as a universal VRAM barrier. It is useful when rapid wake-up matters; process exit remains the maximum-isolation option.

- [vLLM sleep mode](https://docs.vllm.ai/en/stable/features/sleep_mode/)
- [Current vLLM sleep endpoint and V0 completion warning](https://github.com/vllm-project/vllm/blob/735def4fcf39945b6e6c24769878760e3e113b15/vllm/entrypoints/serve/dev/sleep/api_router.py#L20-L47)

### Deno is the closest reviewed bridge to coordinated two-sided handoff

Deno Local LLM Loader explicitly calls:

```python
comfy_model_management.unload_all_models()
comfy_model_management.soft_empty_cache(force=True)
```

It waits briefly, repeats cache cleanup, records before and after state, and then uses provider-specific unload behavior after the LLM call. This is much closer to the actual single-GPU problem than a generic API client. However, those Comfy calls affect only registered `ModelPatcher` models and unused Torch allocator blocks. They do not reset the execution cache or clear raw custom-node objects, module globals, native llama contexts, or other pack-owned retention.

The current default `Auto: unload only before first LLM call` first trusts Deno's own in-memory warm marker, which can outlive the provider's real residency state. For llama.cpp, vLLM, and Custom, its fallback provider check also treats any matching model in `/v1/models` as loaded. Current llama.cpp router lists available models even when their status is unloaded. Either route can skip freeing Comfy's managed models incorrectly. For strict single-GPU handoff, `Always unload before each LLM call` is the safer setting for every provider, not only the OpenAI-compatible ones.

Deno also does not poll Ollama or llama.cpp until VRAM teardown is complete. Its tests mock provider HTTP calls, and no complete live 5090 handoff trace was found.

- [Deno Comfy-side VRAM release](https://github.com/Deno2026/comfyui-deno-custom-nodes/blob/0ec785a685a797f871e3606cfb7e63cc4380c1ef/deno_local_llm_refiner.py#L1043-L1119)
- [Deno provider-loaded detection](https://github.com/Deno2026/comfyui-deno-custom-nodes/blob/0ec785a685a797f871e3606cfb7e63cc4380c1ef/deno_local_llm_refiner.py#L1845-L1869)
- [Deno provider unload methods](https://github.com/Deno2026/comfyui-deno-custom-nodes/blob/0ec785a685a797f871e3606cfb7e63cc4380c1ef/deno_local_llm_refiner.py#L1882-L2013)

### llama-swap provides the strongest reviewed external stop guarantee

Current llama-swap owns its upstream model processes. Its unload path sends a configured stop command or SIGTERM, waits for a graceful timeout, kills the whole process group if required, and waits for process exit. Its scheduler explicitly performs unload synchronously so callers can rely on the targeted processes being stopped after the call returns.

This is stronger than dropping Python references and stronger than an asynchronous model-unload response.

The reviewed Comfy client does not expose the full guarantee cleanly. It does not free Comfy-managed models first, calls unload-all rather than selected-model unload, sets a five-second HTTP timeout against a longer backend stop window, and suppresses exceptions.

- [llama-swap process teardown](https://github.com/mostlygeek/llama-swap/blob/8945d2b766cf0062b20ad8571386705d2355b368/internal/process/process_command.go#L516-L610)
- [llama-swap synchronous scheduler unload](https://github.com/mostlygeek/llama-swap/blob/8945d2b766cf0062b20ad8571386705d2355b368/internal/router/scheduler/fifo.go#L219-L267)
- [llama-swap unload API](https://github.com/mostlygeek/llama-swap/blob/8945d2b766cf0062b20ad8571386705d2355b368/internal/server/api.go#L218-L223)
- [Current ai-joe Comfy client unload](https://github.com/ai-joe-git/comfyui_llama_swap/blob/0ae61519ad61ec7d54558ec4344cc7fa15a714ca/nodes.py#L94-L115)

## Is a ComfyUI Restart Still Required?

| Situation | Expected answer today |
| --- | --- |
| Core `Generate Text` with a supported native model | No. It participates in normal Comfy eviction and manual unload. |
| Custom PyTorch model correctly wrapped in ModelPatcher | Normally no. It is subject to the same lifecycle and any current core bugs. |
| Raw Transformers model with correct `.cpu()` and reference cleanup | Normally no, but Comfy cannot enforce or pressure-evict it. |
| Raw Transformers model retained in a cache or global | Possibly. Pack-specific cleanup or restart may be the only exposed path. |
| In-process llama-cpp with explicit complete close and no references | No in principle. Current backends support it. |
| In-process llama-cpp relying on GC, especially multimodal | Still a real risk. Restart may remain the practical recovery path. |
| External Ollama, LM Studio, vLLM, or llama-server | Do not restart Comfy. Unload, sleep, or stop the external runtime, then wait for completion. |
| Residual small Comfy CUDA-context allocation | Expected while Comfy remains a CUDA process. It is not the LLM model. |

## Comparison With comfyui-llamacpp

### What remains genuinely strong

The project's core design still has a real advantage for arbitrary GGUF work:

- llama.cpp weights, KV cache, vision projector, native buffers, and CUDA context live outside ComfyUI's Python process.
- Stopping the tracked parent of a direct single-model `llama-server` and waiting for that process to exit is the project's strongest path. Python caches and node references cannot keep that process's allocation alive.
- A server crash does not corrupt ComfyUI's Python heap or Torch dependency set.
- The same boundary works for models that cannot be expressed through Comfy's `ModelPatcher` contract.

This advantage is now shared by other external runtimes and by current packs that use per-run subprocesses. The product distinction should be framed as safe, confirmed, workflow-visible llama.cpp lifecycle control, not as the only way any LLM can ever unload.

### Current gaps in our implementation

The current dirty code has three different lifecycle strengths:

1. `Stop llama.cpp Server` terminates the tracked parent process and waits for it, with a force-kill fallback. This is strong for a direct single-process server, but it is not a confirmed process-tree barrier: Linux launches do not create a new process group, normal Windows stop does not close the kill-on-close Job Object, and the later name-based sweep kills matches without waiting for them.
2. Router `unload_model()` posts `/models/unload` and returns on HTTP success. Current llama.cpp handles that endpoint asynchronously, so this is not yet a VRAM completion barrier.
3. Neither server start nor prompt execution first invokes Comfy's managed-model unload path. An external server can therefore be asked to allocate while Comfy still holds warm diffusion models.

There is also a serious ownership defect already identified in the broader review: `_kill_orphaned_servers()` kills every process whose name contains `llama-server`, not only processes created by this plugin. That makes current cleanup powerful but unsafe.

Primary local evidence:

- `server_manager.py:286-297`, broad process sweep.
- `server_manager.py:479-527`, synchronous owned-process stop plus broad sweep.
- `server_manager.py:783-834`, router unload on HTTP response without status polling.

### Requirements for a future strict handoff

These are research conclusions, not implemented changes:

1. Track and stop only processes owned by this plugin, including their process groups or Windows Job Object membership.
2. Before starting or waking the LLM on a shared GPU, synchronously unload Comfy-managed models and clear unused Torch allocator blocks.
3. After router unload, poll `/models` or consume `/models/sse` until the target is actually `unloaded`.
4. After process stop, verify process exit before signaling downstream nodes.
5. Distinguish "unload requested" from "VRAM handoff complete" in node outputs and status.
6. Expose current llama-server idle sleep and router residency controls where they fit the workflow.
7. Add a real single-GPU test that alternates a large Comfy model and llama-server repeatedly without restarting either host.

## Practical Recommendation for This RTX 5090 Workstation

For a single 32 GB GPU, assume that a substantial diffusion or video model and a useful local LLM cannot both remain resident safely.

### Best current choices by need

1. Use core `Generate Text` when its supported model family and workflow controls are sufficient. This has the cleanest Comfy-native memory behavior.
2. For broad GGUF coverage, prefer an external runtime or a per-run subprocess with an explicit completion barrier.
3. If trying Deno for a strict single-GPU handoff, select `Always unload before each LLM call` for every provider. Treat its post-run provider response as an unload request, not proof of VRAM recovery, and remember that it cannot clear unmanaged Comfy custom-node objects.
4. With Ollama, set `keep_alive` to zero when immediate release is wanted, then require both `/api/ps` disappearance and driver-visible free-memory recovery before a tightly packed diffusion stage.
5. With LM Studio, use its native unload endpoint or a short TTL. Do not assume a generic OpenAI-compatible node controls model residency.
6. Avoid LLM Party's direct GGUF route when reliable VRAM handoff is the priority.

### Correct operation order

```text
Unload Comfy-managed models
  -> verify Comfy free memory increased
  -> start, load, or wake the local LLM
  -> run inference
  -> request LLM unload or stop its process
  -> wait for provider status or process-exit confirmation
  -> confirm driver-visible free memory reached the expected baseline
  -> start the next GPU-heavy Comfy stage
```

### What to test before trusting a node pack

Use the same sequence for every candidate:

1. Record idle process VRAM and Comfy's Torch allocated and reserved bytes.
2. Run one text-only request.
3. Run one multimodal request if vision matters.
4. Invoke the pack's documented unload control.
5. Confirm model status at the owning runtime, not just a successful HTTP response.
6. Confirm driver-visible memory falls to the expected process baseline.
7. Immediately load a large diffusion or video model.
8. Repeat the LLM to diffusion sequence at least ten times.
9. Repeat cancellation and error paths, which often bypass cleanup.
10. Distinguish a stable CUDA-context baseline from model-sized retained memory.

The decisive success criterion is not "the unload button returned success." It is that the next large allocation succeeds repeatedly without process restart, CPU fallback, or growing residual VRAM.

## Current Core Limitations and Uncertainty

Comfy's managed path is substantive but not flawless.

- An open `free_memory()` race report describes list mutation under heavily threaded checkpoint swapping. It is a concurrency defect, not evidence that the architecture cannot unload ordinary managed models.
- Custom packs can change global Torch or cuDNN settings and affect core behavior even when their nodes are not in the active workflow.
- External unload timing differs by runtime and version.
- No reviewed project supplied a current, repeatable, cross-runtime RTX 5090 trace that measures both Comfy and external-runtime ownership through a full alternating workflow.

Sources:

- [Current Comfy `free_memory()` race report](https://github.com/Comfy-Org/ComfyUI/issues/14365)
- [Custom-node global side-effect diagnosis](https://github.com/Comfy-Org/ComfyUI/issues/14378)

## Final Assessment

The historical project rationale should be updated, not discarded.

Observed facts:

- ComfyUI now has first-party, diffusion-style LLM/VLM memory management for supported native models.
- Third-party packs can opt into the same model manager, as Florence2 demonstrates.
- Most general local LLM packs still use raw Transformers or llama-cpp objects and must implement their own teardown.
- Modern llama-cpp-python can close an in-process model correctly, so leakage is not inevitable.
- Current external runtimes have real unload, sleep, TTL, and process-management mechanisms.
- Many Comfy bridges do not coordinate both VRAM owners or wait for teardown to finish.

Recommendation:

> Keep the external llama-server architecture as a central strength of comfyui-llamacpp, but modernize the claim. The goal is not merely "unload without restarting ComfyUI." Current Comfy can now do that for managed models. The stronger target is a safe and confirmed two-sided VRAM handoff for arbitrary local GGUF models, with owned-process teardown, current router semantics, explicit completion status, and repeatable single-GPU proof.
