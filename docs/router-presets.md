# Local router presets

`Start llama.cpp Router` accepts a **Local Model Preset File** and a **Preset
Settings Policy**. The file uses llama.cpp's INI format and names models already
on the machine running ComfyUI. This feature requires a server advertising
`--models-preset` and `--offline`; llama.cpp b9957 provides both.

Leave the file empty to preserve existing router behavior. Both new inputs are
appended after the previous inputs, so saved widget positions stay unchanged.
The default policy is `override`.

## Example

Save the following beside `small.gguf`, `vision.gguf` and `vision-mmproj.gguf`:

```ini
version = 1

[*]
threads = 8

[small-text]
model = small.gguf
ctx-size = 4096
gpu-layers = 20
no-mmproj = true

[vision]
model = vision.gguf
mmproj = vision-mmproj.gguf
ctx-size = 8192
gpu-layers = 32
```

Select this INI file and choose `inherit` to use its per-model settings. Relative
model, projector and draft-model paths resolve against the **resolved INI file's
directory**, including when the source is a symlink. Absolute paths work too.
Use unquoted paths, including paths containing spaces. `;` and `#` begin comments,
so those characters cannot be used in file paths. All referenced weights must
be existing local `.gguf` files. Paths are interpreted on the ComfyUI host, not
the browser's machine. A Windows-hosted ComfyUI needs Windows-visible paths.

With a preset selected, `(auto)` under **Models Directory** uses the preset
without adding an automatically chosen model directory. Selecting a specific
configured directory includes that directory as an additional local source.
llama.cpp can also list previously cached models; offline mode prevents it from
downloading missing data. Select the exact model ID returned by router discovery
for generation or scoped unload. No model name is guessed from its file path.

## Precedence

| Policy | Node settings sent to llama-server |
| --- | --- |
| `override` | Preserves existing context, GPU layers, main GPU, thread, batch, tensor split, flash-attention, mmap, idle and fit settings. These tuning options override the INI when emitted on the command line. |
| `inherit` | Omits those per-model options from the command line. Named model options override `[*]`, then unspecified settings use llama.cpp defaults. |

Both policies retain router host, port, maximum models, autoload policy, API key
file and media directory. Explicit supported **Extra Arguments** override INI
settings in both modes. `context_size = 0` is an explicit upstream-auto setting;
it does not mean inherit a model's INI context size. Use the `inherit` policy for
that behavior. Inherit requires a preset file.

Model and projector paths belong in each model's INI section. b9957 removes the
router's global model/projector arguments before applying CLI settings to its
children; a global projector path does not override a per-model INI path.

The initial policy applies to the overlapping settings as a group. To override
only one model, edit its INI section. Old workflows retain `override` and their
original command-line defaults. Settings omitted by legacy behavior, such as an
empty thread value or the false legacy mmap/flash toggle, still allow the INI
to supply that setting.

## Validation and supported options

This integration accepts a bounded subset of the
[b9957 preset format](https://github.com/ggml-org/llama.cpp/blob/c4ae9a88f8884ee5a155c8349ace9ea31a58007f/common/preset.cpp).
It checks every section, including `[*]` and a section named `default`. Only
`[*]` is shared configuration; `default` is an ordinary named model. Each model
must have a local `model` path, directly or inherited from `[*]`.

Supported canonical keys:

| Purpose | Keys |
| --- | --- |
| Local weights | `model`, `mmproj`, `spec-draft-model` |
| Context and offload | `ctx-size`, `gpu-layers`, `main-gpu`, `tensor-split`, `fit` |
| Execution | `threads`, `batch-size`, `ubatch-size`, `parallel`, `flash-attn`, `mmap`, `mmproj-auto`, `mmproj-offload`, `jinja`, `sleep-idle-seconds` |
| Cache | `cache-ram`, `cache-type-k`, `cache-type-v`, `ctx-checkpoints` |
| Sampling | `temp`, `top-k`, `top-p`, `min-p`, `repeat-penalty`, `seed`, `reasoning-budget` |
| Speculation | `spec-type`, `spec-draft-n-max`, `spec-draft-n-min` |
| Preset lifecycle | `load-on-startup`, `stop-timeout` |

Common short forms such as `m`, `mm`, `md`, `c`, `ngl`, `mg`, `t`, `b`, `ub`,
`np`, `ts`, `fa`, `cram`, `ctk`, `ctv` and `ctxcp` are accepted. Negative Boolean
forms include `no-mmap`, `no-mmproj`, `no-mmproj-auto`, `no-mmproj-offload` and
`no-jinja`. Supported environment-style names, such as `LLAMA_ARG_MODEL`,
`LLAMA_ARG_CTX_SIZE` and `LLAMA_ARG_N_GPU_LAYERS`, are normalized too. Duplicate
sections or aliases for the same option in one section are rejected. Quantization
suffixes after a colon are normalized as llama.cpp does; colliding names fail.

Boolean values accept `true/false`, `yes/no`, `on/off` or `1/0`. Flash attention
accepts `on/off/auto`; fit accepts `on/off`. Numeric values must be finite and in
the supported range. Presets are limited to 256 KiB of UTF-8 and 128 named models.
There is no interpolation, include mechanism, quoted-value syntax or multiline
value syntax. Unsupported keys produce an error naming the key and this guide.
Unknown options are rejected even if a newer llama.cpp binary supports them.

Model acquisition options are rejected, including HF repositories/files/tokens,
URLs, Docker sources, remote draft/vocoder sources and convenience model presets.
The same policy covers aliases and environment-style spellings in every section.
Credentials, host/port, nested preset files, tools and model aliases belong outside
this supported INI subset. Named sections provide the exact router identities.

When a preset is selected, Extra Arguments are also limited to the supported
local tuning options above, plus `--metrics`, `--props`, `--log-timestamps`,
`--log-prefix` and `--offline`. Existing typed options remain reserved for their
node fields; INI-only lifecycle keys cannot be passed as command-line flags.
The integration forces `--offline` and removes inherited `LLAMA_ARG_*` and
`LLAMA_API_KEY` variables from the server environment. Configure credentials
through the existing API key file/client key inputs. Platform and CUDA variables
remain available. These restrictions apply to preset launches; ordinary launches
keep their existing behavior.

## Edits, provenance and lifecycle

Requeue the Start Router node after editing the file. Startup rereads and validates
the file before replacing a healthy owned runtime. Its content SHA-256 participates
in configuration identity, so edits at the same path trigger replacement. An
unchanged file/configuration reuses a healthy router. Invalid input, unsupported
binary options or failure to stage the file leave the old runtime serving.

The router reads an owned temporary snapshot containing normalized paths and
validated settings. Catalog reload uses that snapshot, not later source edits.
The snapshot stays present while the router is owned, including after model-only
unload or an incomplete process stop. It is removed after complete shutdown,
successful replacement or failed startup cleanup. The source INI is never edited
or removed. An ungraceful host crash may leave a small temporary snapshot file.

Runtime status records `models_preset`, `preset_policy`, `preset_sha256`,
`preset_effective_sha256` (the normalized snapshot hash) and `preset_models`.
Configuration fields show the saved node choices; under inherit,
the overlapping choices listed above are omitted from the actual launch command.
Use Model Info for a loaded model's effective server settings. Validation confirms
file existence, option syntax and advertised binary flags; it cannot guarantee
that arbitrary weights/projectors fit memory or are compatible with each other.

Preset selection does not change generation routing, cancellation, model-specific
unload, ownership checks or terminal release semantics. Existing runtime/browser
verification remains required for a new binary or model combination.
