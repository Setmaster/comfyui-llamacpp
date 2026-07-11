# Migrating from 0.2.1 to 0.3

Version 0.3 is a compatibility-preserving runtime refactor. The public V1 node
surface remains because current ComfyUI still supports it and saved workflow
compatibility is more important than a premature V3 conversion.

## Preserved contracts

The automated compatibility fixture is anchored to 0.2.1 commit
`1e3b7a5a2d90ee3a40cf40e01784de1c343ea85a`. It preserves:

- Every released node class ID and display name.
- `FUNCTION`, return types, return names, and output order.
- Released socket names and defaults.
- The complete legacy primitive widget prefix for each node.
- Root compatibility imports from `server_manager`, `model_manager`, and
  `streaming_client`.
- Ten backend-declared image sockets on both VLM prompt nodes.

New primitive widgets are appended after the final legacy primitive widget.
This prevents an old saved value from being reinterpreted as a different
setting.

## Behavior changes

### Process ownership

0.2.1 could search for and terminate processes whose names contained
`llama-server`. Version 0.3 never does that. It controls only the exact process
tree it started and identified.

- Linux uses a unique owned process group, a stable pidfd generation handle,
  and kernel-backed abrupt-owner protection. It requires functional
  `pidfd_open` and `waitid(P_PIDFD)` support, normally Linux 5.4 or newer.
  Unsupported Linux hosts are rejected before a server is spawned. Other POSIX
  systems retain exact process-group cleanup for normal stop and exit, without
  the Linux abrupt-owner guarantee.
- Windows uses a fresh Job Object with kill-on-close when assignment succeeds.
- A validated Windows descendant fallback is used only when Job assignment is
  unavailable, and status reports that degraded mode.

### Comfy native unload

Successful Comfy `POST /free` and `POST /api/free` calls now enter this pack's
lifecycle coordinator:

- Direct mode stops the owned process tree.
- Router mode unloads every resident model to a terminal state and normally
  keeps the router process.
- Attached endpoints are untouched.
- Active managed generation defers implicit release until its lease ends.

Explicit start, stop, router load, and router unload now reject before mutation
while managed generation is active. Native Comfy release remains the safe
deferred path.

The explicit Stop, Release, Load Model, and Unload Model nodes remain.

### Router API

Version 0.3 uses current llama.cpp contracts:

- `POST /models/load`
- `POST /models/unload`
- `GET /models` as the terminal-state authority
- `GET /props?model=<id>`
- `POST /tokenize`

It does not use `POST /models` for loading because current llama.cpp reserves
that endpoint for other operations. Model selection resolves exact IDs and
aliases returned by the router and fails on ambiguity.

The List Models node can request a current router catalog rescan through
`GET /models?reload=1`.

Current llama.cpp exposes one `--models-dir` root and scans GGUF files at that
root plus one bundle-directory level. The Router node's appended
`models_directory` widget defaults to `(auto)`, which selects the configured
Comfy root with the most safe, unambiguous models visible under that rule.
Select a specific configured root when several collections are available.

Each immediate directory represents one logical router model and must contain
one base GGUF, or one complete shard set, plus at most one matching projector.
Multiple quantizations or projectors in one directory are selected by upstream
in unspecified filesystem order, so this pack excludes them from auto-root
scoring and does not claim an exact mapping for them. Deeper models are
invisible to the native router. Direct dropdowns still scan recursively across
all configured roots; use List Models for the active router's canonical IDs.

### Advanced launch arguments

`extra_args` remains available for llama.cpp tuning flags that do not have a
dedicated field. It can no longer duplicate typed model, transport, GPU,
authentication, TLS, router, or lifecycle options. Move any such legacy
duplicate to the corresponding node widget. This prevents the manager from
checking one endpoint or ownership configuration while the process runs
another.

Symbolic GPU-layer values (`auto` and `all`) are accepted only when the probed
binary help explicitly advertises them. Numeric values remain portable across
older builds.

### Model discovery

The default remains `ComfyUI/models/LLM/gguf`. Version 0.3 also honors Comfy
`LLM` or `llm` roots from `extra_model_paths.yaml`, using their `gguf` child
when present. Model and projector paths must remain inside configured roots.

### Flash Attention

The legacy Boolean widget is preserved. `true` is translated to current
llama.cpp's explicit `-fa on`, rather than the obsolete bare `-fa` form.
The new `flash_attention_mode` widget can request `auto`, `on`, or `off` when
the probed binary supports it.

### Prompt cache

`keep_context` was previously described as conversation context. Its actual
wire behavior is llama.cpp `cache_prompt`: reuse of a matching prompt-prefix KV
cache. It does not store messages between unrelated prompts and it is not
durable chat history.

### VLM inputs

All ten `image_1` through `image_10` sockets now exist in Python. The frontend
only changes their visibility. `image_amount=0` remains zero, and save/reload no
longer depends on a prototype-level frontend patch. By default only the first
image in each Comfy batch is sent, preserving old behavior. Enable
`include_image_batch` to send every batch item.

### Streaming

Streaming now has an overall deadline, closes every HTTP response, respects
Comfy interrupts, requires a valid terminal marker, and preserves partial
output with failure metadata. Successful and partial content now preserves
leading and trailing whitespace exactly.

## New nodes

- **llama.cpp Connection**
- **Release llama.cpp VRAM**
- **llama.cpp Token Count**
- **llama.cpp Model Info**
- **llama.cpp Structured Output**

## Upgrade procedure

1. Preserve a copy of important workflows.
2. Switch the plugin checkout to `dev` and update dependencies.
3. Update `llama-server` to a current build.
4. Restart ComfyUI and confirm 17 nodes register.
5. Open each old workflow and verify model selections and visible image count.
6. Queue one direct text workflow, one router workflow if used, and one VLM
   workflow if used.
7. Run the native unload and explicit release checks in
   [user-acceptance.md](user-acceptance.md).

## Rollback

The 0.3 work is isolated on `dev`. To return the plugin checkout to 0.2.1:

```bash
git fetch origin
git switch master
git pull --ff-only origin master
```

Restart ComfyUI after switching. Workflows saved with new 0.3-only nodes will
show those nodes as missing on 0.2.1; released legacy nodes remain compatible.

Switching the plugin does not switch the external llama.cpp binary. On the
maintainer Windows host, follow the separate
[pinned runtime rollback procedure](troubleshooting.md#roll-back-the-pinned-windows-runtime)
to exchange the complete b9957 and b8261 directories without overlaying them.
