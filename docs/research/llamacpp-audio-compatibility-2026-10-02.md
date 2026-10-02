# Bounded audio compatibility investigation

Date: 2026-10-02

Issue: ENG-90

Status: Positive short-clip ASR compatibility verified; public audio feature deferred.

Runtime target: Windows `C:\llama`, llama.cpp b9957, commit
`c4ae9a88f8884ee5a155c8349ace9ea31a58007f`.

## Current result and decision boundary

The initial installed model inventory contained no complete audio-capable model/projector
pair. The user subsequently approved one pinned Qwen3-ASR pair; its positive trial
is recorded below. Audio input is implemented in the pinned server, but a vision projector does
not provide audio support. A parent-owned live Qwen3-VL probe confirmed rejection
through both audio endpoints and a healthy server afterward. The current product
has no public `AUDIO` input contract.
Keep public audio support deferred. The successful, narrowly scoped transcription
trial establishes a supported experimental combination; it does not establish
general audio understanding, audio generation, real-time audio ingestion, or a
shipping Comfy node contract.

The smallest candidate found in the pinned build's documented preconverted audio
families is Qwen3-ASR 0.6B with its matching Q8 audio projector. Their combined download is 1,019,141,728 bytes (1.02 GB, 0.949 GiB).
The user approved that exact acquisition and local trial on 2026-10-02. Both files
were downloaded from the immutable revision below and matched their pinned sizes
and SHA-256 hashes before being installed.

The research sub-agent started or stopped no apps or servers. The parent ran the
approved positive trial using the project's owned Windows process controller.
The earlier research-only phase downloaded no weights, tensor payloads, dependencies
or audio samples; it used read-only metadata and anonymous file HEAD requests.

## Installed inventory

Root: `F:\models\LLM\gguf`, inspected through `/mnt/f/models/LLM/gguf`.
Before the approved acquisition, all 32 contained GGUF files parsed successfully: 24 model files and eight
projectors. The bounded header reader scanned 197,386,496 header bytes, without
reading tensor payloads. Its process-local metadata selector was extended to
retain `clip.has_audio_encoder` and `clip.audio.*`; product code was unchanged.

| Installed model family | Model files | Header architecture | Audio feasibility |
| --- | ---: | --- | --- |
| Gemma 3 12B/27B and derivatives | 6 | `gemma3` | Available matching projectors are vision only. |
| Gemma 4 26B-A4B / 31B derivatives | 2 | `gemma4` | These variants are text/vision; no Gemma 4 projector is installed. |
| Qwen3-VL 4B/8B and derivatives | 5 | `qwen3vl` | Available projectors are vision only. This is not Qwen3-ASR. |
| Qwen3.5 4B/9B/27B | 5 | `qwen35` | Available projectors are vision only. |
| MiniCPM-V 4.5 Q5/Q6 | 2 | `qwen3` | Installed resampler projector is vision only; MiniCPM-V is not MiniCPM-o. |
| Dark-Nexus 32B | 1 | `qwen3` | No audio projector. |
| Three roleplay 24B derivatives | 3 | `llama` | No audio projector. |

All eight projectors have `clip.has_vision_encoder=true`. None has a
`clip.has_audio_encoder` key or any `clip.audio.*` metadata:

| Relative projector path | `clip.projector_type` |
| --- | --- |
| `gemma/Gemma-3-27B-Derestricted.mmproj-f16.gguf` | `gemma3` |
| `gemma-3-12b/gemma-3-12b-it-abliterated.mmproj-Q8_0.gguf` | `gemma3` |
| `minicpm-v-4_5-bakeoff/mmproj-model-f16.gguf` | `resampler` |
| `qwen-vl-4B-Instruct/Qwen3-VL-4B-Instruct-abliterated-v1-mmproj-Q8_0.gguf` | `qwen3vl_merger` |
| `qwen-vl-8B-Instruct/Qwen3-VL-8B-Instruct-abliterated-v2.0.mmproj-Q8_0.gguf` | `qwen3vl_merger` |
| `qwen3-vl-4b-instruct-bakeoff/mmproj-Qwen_Qwen3-VL-4B-Instruct-f16.gguf` | `qwen3vl_merger` |
| `qwen3.5-27b/mmproj-F32.gguf` | `qwen3vl_merger` |
| `qwen3.5-4b-bakeoff/mmproj-Qwen_Qwen3.5-4B-f16.gguf` | `qwen3vl_merger` |

The pinned loader defaults a missing audio-encoder flag to false and constructs an
audio context only when that flag is true. Runtime audio capability requires that
context. Thus the result follows from retained metadata and loader behavior, not
the filename or presence of an arbitrary mmproj.
[Loader flags and context construction](https://github.com/ggml-org/llama.cpp/blob/c4ae9a88f8884ee5a155c8349ace9ea31a58007f/tools/mtmd/clip.cpp#L1092),
[runtime capability predicate](https://github.com/ggml-org/llama.cpp/blob/c4ae9a88f8884ee5a155c8349ace9ea31a58007f/tools/mtmd/mtmd.cpp#L1686).

Google's current Gemma 4 overview identifies native audio in E2B, E4B and 12B,
excluding the installed 26B-A4B and 31B variants. The pinned llama.cpp support list
specifically lists E2B/E4B under mixed modalities. Do not extrapolate support for
other/newer variants from Google's current list.
[Google variant overview](https://ai.google.dev/gemma/docs/core),
[pinned supported families](https://github.com/ggml-org/llama.cpp/blob/c4ae9a88f8884ee5a155c8349ace9ea31a58007f/docs/multimodal.md).

## Candidate, access and resource bounds

Upstream conversion: `ggml-org/Qwen3-ASR-0.6B-GGUF`, revision
`928ab958557df9aa2ef1c93e0e83c7ad0933fae2`. Anonymous model API inspection returned
`private=false`, `gated=false`, `disabled=false`. Anonymous HEAD requests for both
immutable files returned HTTP 200 and the lengths below during the initial
research-only phase. The parent subsequently downloaded both verified files.
[Metadata API](https://huggingface.co/api/models/ggml-org/Qwen3-ASR-0.6B-GGUF?blobs=true).

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| [Qwen3-ASR-0.6B-Q8_0.gguf](https://huggingface.co/ggml-org/Qwen3-ASR-0.6B-GGUF/resolve/928ab958557df9aa2ef1c93e0e83c7ad0933fae2/Qwen3-ASR-0.6B-Q8_0.gguf) | 804,749,248 | `bca259818b50ca7c4c05e9bdb35a5dc04fa039653a6d6f3f0f331f96f6aa1971` |
| [mmproj-Qwen3-ASR-0.6B-Q8_0.gguf](https://huggingface.co/ggml-org/Qwen3-ASR-0.6B-GGUF/resolve/928ab958557df9aa2ef1c93e0e83c7ad0933fae2/mmproj-Qwen3-ASR-0.6B-Q8_0.gguf) | 214,392,480 | `41a342b5e4c514e968cb756de6cd1b7be39eff43c44c57a2ef5fc6522e36603d` |

Qwen identifies this as speech recognition, with Apache-2.0 licensing. Pinned
conversion code registers the ASR text model as `qwen3vl`, but explicitly creates
an audio-only `qwen3a` projector. Shared text architecture with installed Qwen3-VL
does not make those existing models interchangeable. The downloaded headers confirm `general.architecture=qwen3vl` for the model and
`clip.has_audio_encoder=true`, `clip.audio.projector_type=qwen3a` for the projector.
The resulting runtime reports audio true and vision/video false.
[Official model card](https://huggingface.co/Qwen/Qwen3-ASR-0.6B),
[pinned projector conversion](https://github.com/ggml-org/llama.cpp/blob/c4ae9a88f8884ee5a155c8349ace9ea31a58007f/conversion/qwen3vl.py#L219),
[pinned text conversion](https://github.com/ggml-org/llama.cpp/blob/c4ae9a88f8884ee5a155c8349ace9ea31a58007f/conversion/qwen3vl.py#L340).

Planning estimate only: with all weights offloaded, a 4,096-token context and one
request, allow approximately 2-3 GiB GPU headroom and measure the actual peak.
The 0.949 GiB file total is a rough weight-size proxy, not measured allocation.
The model config gives 28 layers, eight KV heads and head dimension 128; standard
f16 K/V at 4,096 tokens adds about 448 MiB
(`2 * 28 * 8 * 128 * 4096 * 2` bytes), before compute buffers, audio activations,
CUDA context and allocator overhead. This is not a hard VRAM ceiling.
[Official configuration](https://huggingface.co/Qwen/Qwen3-ASR-0.6B/blob/main/config.json).

After approval, hash/header verification and an unused-port check, the parent
executed this isolated single-slot launch recipe:

```powershell
& C:\llama\llama-server.exe `
  -m 'F:\models\LLM\gguf\qwen3-asr-0.6b\Qwen3-ASR-0.6B-Q8_0.gguf' `
  --mmproj 'F:\models\LLM\gguf\qwen3-asr-0.6b\mmproj-Qwen3-ASR-0.6B-Q8_0.gguf' `
  --host 127.0.0.1 --port 18890 --alias eng90-qwen3-asr `
  -c 4096 -np 1 -ngl 99 -b 512 -ub 128 --slots
```

Use an owned process handle/job and the project's normal cleanup path. The pinned
runtime offloads the projector by default; `--no-mmproj-offload` is an optional
CPU-projector comparison, not required for the initial trial. Do not use `-hf` in
this launch recipe, since acquisition and hash verification are separate steps.

## Pinned request and response contract

`GET /props?autoload=false` exposes `modalities.audio`. It must be true before a
positive trial. A true value is a capability signal, not a transcription quality
result. The pinned server exposes both routes below.
[Properties](https://github.com/ggml-org/llama.cpp/blob/c4ae9a88f8884ee5a155c8349ace9ea31a58007f/tools/server/server-context.cpp#L4531),
[routes](https://github.com/ggml-org/llama.cpp/blob/c4ae9a88f8884ee5a155c8349ace9ea31a58007f/tools/server/server.cpp#L240).

For `POST /v1/chat/completions`, send a complete WAV file as raw base64:

```json
{
  "model": "eng90-qwen3-asr",
  "messages": [{"role": "user", "content": [{
    "type": "input_audio",
    "input_audio": {"data": "<base64 of complete WAV bytes>", "format": "wav"}
  }]}],
  "temperature": 0,
  "max_tokens": 128,
  "stream": false
}
```

Read `choices[0].message.content`; for a streamed request, concatenate
`choices[0].delta.content` and preserve the raw events. The pinned parser accepts
`input_audio`, not the `audio_url` example used in the official model's separate
vLLM integration. The `format` field is ignored; file bytes determine decoding.
The built-in decoder handles WAV, MP3 and FLAC, downmixing/resampling to the audio
encoder's required format. Use inline base64 in this spike, without external URL
fetching or local-file access through the server.
[Chat contract](https://github.com/ggml-org/llama.cpp/blob/c4ae9a88f8884ee5a155c8349ace9ea31a58007f/tools/server/README.md#L1236),
[parser](https://github.com/ggml-org/llama.cpp/blob/c4ae9a88f8884ee5a155c8349ace9ea31a58007f/tools/server/server-common.cpp#L999),
[audio decoding](https://github.com/ggml-org/llama.cpp/blob/c4ae9a88f8884ee5a155c8349ace9ea31a58007f/tools/mtmd/mtmd-helper.cpp#L441).

For `POST /v1/audio/transcriptions`, send multipart form data. An equivalent
bounded curl recipe is below; parent supplies its owned endpoint and fixture:

```sh
curl --max-time 60 --silent --show-error \
  http://127.0.0.1:18890/v1/audio/transcriptions \
  -F 'file=@fixture.wav;type=audio/wav' \
  -F 'model=eng90-qwen3-asr' -F 'response_format=json' \
  -F 'temperature=0' -F 'max_tokens=128' -F 'stream=false'
```

The required field is `file`; `response_format=json` is the only supported format.
`stream`, `temperature` and `max_tokens` arrive as form strings. Omitting `prompt`
uses the runtime's default transcription instruction. Avoid assuming that
language hints, timestamps or vLLM's output parser carry over to this build.
The final response is `{"type":"transcript.text.done","text":...,"usage":...}`;
streaming emits `transcript.text.delta` with `delta`, then the final event.
The server places generated content in these fields without an ASR-specific
cleanup pass. Preserve any language markers or model control tokens in evidence
before deciding what a future product adapter should expose.
[Multipart conversion](https://github.com/ggml-org/llama.cpp/blob/c4ae9a88f8884ee5a155c8349ace9ea31a58007f/tools/server/server-chat.cpp#L580),
[default prompt](https://github.com/ggml-org/llama.cpp/blob/c4ae9a88f8884ee5a155c8349ace9ea31a58007f/common/chat.cpp#L681),
[final output](https://github.com/ggml-org/llama.cpp/blob/c4ae9a88f8884ee5a155c8349ace9ea31a58007f/tools/server/server-task.cpp#L740),
[stream delta](https://github.com/ggml-org/llama.cpp/blob/c4ae9a88f8884ee5a155c8349ace9ea31a58007f/tools/server/server-task.cpp#L1340).

These source-verified transport recipes were exercised in the positive trial
below. The raw ASR control prefix is part of the observed response contract.

## Recorded negative case and speech fixture

The parent tested the actual Windows b9957 server with Qwen3-VL 4B Q5 and its Q8
vision projector. `/props` reported vision/video true and audio false. Both
requests contained the valid spoken WAV described below:

| Probe | Observed result | Elapsed |
| --- | --- | ---: |
| Chat `input_audio` | HTTP 500 `server_error`, audio input unsupported with missing-mmproj hint | 0.0061 s |
| Multipart transcription | HTTP 501 `not_supported_error`, model does not support audio input | 0.0025 s |
| Health afterward | HTTP 200, `status=ok` | Not recorded |

Evidence: Project KB
`~/agent-prj-data/prj_data/github.com/Setmaster/comfyui-llamacpp/evidence/2026-10-02/backlog/audio-unsupported-qwen.json`.
The researcher read the saved response evidence. This is an empirical unsupported
combination, not merely a metadata inference. The chat HTTP 500 is the pinned
upstream behavior, not a transport timeout or a successful text-only fallback.

The parent generated `audio-fixture.wav` offline with the installed voice
`HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech\Voices\Tokens\TTS_MS_EN-US_DAVID_11.0`.
Ground truth: `The blue notebook is beside the window.` No speaker playback was
used. The saved `audio-fixture.json` records mono 16 kHz PCM16 and 2.795 seconds.
Independent read-only verification of the saved WAV confirmed:

- Size: 89,486 bytes.
- Frames: 44,720 at 16 kHz, matching the recorded duration.
- SHA-256: `4d141e9f9d520d95620d98f12719c77e36d49ddca4653e50b7c52c75c344598b`.
- RMS: 2,969.39 PCM16 units; peak absolute sample: 31,000, confirming non-silence.

Both fixture files are in the same Project KB evidence folder. This fixture was
used in the approved positive trial below.

## Fixture reproduction and probe protocol

The following is a reproduction template, not an exact transcript of the parent's
fixture-generation command. It uses an installed English Windows SAPI voice and
requires no microphone, private recording, network TTS or new dependency. Record
the exact selected voice ID. The destination must be within an owned probe folder.

```powershell
$speechPath = 'C:\path-to-owned-probe-folder\fixture.wav'
$speechVoice = New-Object -ComObject SAPI.SpVoice
$speechFile = New-Object -ComObject SAPI.SpFileStream
$englishVoices = $speechVoice.GetVoices('Language=409')
if ($englishVoices.Count -eq 0) { throw 'No installed US English SAPI voice' }
$speechVoice.Voice = $englishVoices.Item(0)
$speechVoice.Voice.Id
$speechVoice.Rate = 0
$speechVoice.AllowAudioOutputFormatChangesOnNextSet = $false
$speechFile.Format.Type = 18 # SAFT16kHz16BitMono
try {
  $speechFile.Open($speechPath, 3, $false) # SSFMCreateForWrite
  $speechVoice.AudioOutputStream = $speechFile
  [void]$speechVoice.Speak('The blue notebook is beside the window.')
} finally {
  $speechFile.Close()
  [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($speechFile)
  [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($speechVoice)
}
```

SAPI file output does not play through the speakers. Fixing the voice and text
makes the fixture reproducible on this host, not byte-identical across Windows
voice versions. Record SHA-256, sample rate, channels, sample width, duration and
nonzero sample energy. Reject a silent or malformed fixture. Keep the first trial
to one mono PCM16 WAV, 16 kHz, at most ten seconds and 128 output tokens.
[Microsoft file-output contract](https://learn.microsoft.com/en-us/previous-versions/windows/desktop/ee125635(v=vs.85)),
[SAPI format values](https://learn.microsoft.com/en-us/previous-versions/windows/desktop/ms720595(v=vs.85)).

Parent-owned probe sequence:

1. Completed: installed Qwen3-VL negative case and fixture validation, recorded
   above. This establishes unsupported input behavior, not audio inference support.
2. After the approved acquisition, verify both candidate hashes and audio metadata,
   record binary version, launch arguments, owned PID/job and pre-load VRAM, then
   load only the selected pair. Require `modalities.audio=true`.
3. Submit the spoken fixture through both nonstreaming routes with a 60-second
   absolute request budget. Save status, raw output, duration and logs. Normalize
   case/punctuation/whitespace only for the comparison; require the seven spoken
   words in order with no substituted words. Report any extra language/control
   prefix separately. Run a second request to check reuse.
4. Repeat through chat SSE and compare assembled text with the nonstreaming result.
   This tests streamed text output from a complete audio upload, not incremental
   microphone audio ingestion.
5. Exercise explicit cancellation using a unique `X-Conversation-Id`, as described
   below. Inspect actual task/slot termination and run a fresh request afterward.
6. Release the owned runtime and verify process/job termination, listener closure,
   no active producer, and VRAM return toward the measured pre-load baseline. Record
   a bounded 30-second cleanup observation and explain any residual allocation.
   Do not terminate an attached or unrelated server.

The transcription rejection is explicit in the pinned handler and maps to HTTP
501. The chat error text is checked before media decoding; record its actual HTTP
status rather than assuming parity with the transcription route.
[Transcription guard](https://github.com/ggml-org/llama.cpp/blob/c4ae9a88f8884ee5a155c8349ace9ea31a58007f/tools/server/server-context.cpp#L4752),
[error mapping](https://github.com/ggml-org/llama.cpp/blob/c4ae9a88f8884ee5a155c8349ace9ea31a58007f/tools/server/server-common.cpp#L41).

For cancellation, a client disconnect alone is insufficient: an identified stream
can continue producing into the resumable buffer. Probe stream-control support
first, attach an unpredictable owned ID to the streaming request, issue
`DELETE /v1/stream/<id>`, and check `POST /v1/streams/lookup` with
`{"conversation_ids":["<id>"]}`. DELETE returns 204 even when the session never
existed, so neither that status nor an empty lookup alone proves an active task
was cancelled. Require prior evidence of an active request, then producer/slot
quiescence, reader termination and a successful subsequent request. If the short
clip finishes before cancellation, record the race as inconclusive and make one
bounded retry during processing. Measure whether audio encoding/prefill itself
delays cancellation; generic text cancellation does not prove that phase is
interruptible. Always DELETE the exact owned ID and close the response in cleanup.
[Producer attachment](https://github.com/ggml-org/llama.cpp/blob/c4ae9a88f8884ee5a155c8349ace9ea31a58007f/tools/server/server-context.cpp#L4290),
[stream cancellation and cleanup](https://github.com/ggml-org/llama.cpp/blob/c4ae9a88f8884ee5a155c8349ace9ea31a58007f/tools/server/server-stream.cpp#L630).

## Compatibility matrix

| Combination or behavior | Source/inventory result | Live result |
| --- | --- | --- |
| Installed Qwen3-VL 4B Q5 + Q8 merger projector | Vision only; no audio encoder | Both audio routes rejected; health remained usable |
| Other installed Qwen3-VL / Qwen3.5 + merger projectors | Vision only; no audio encoder | Not individually probed |
| Installed Gemma 3 + installed Gemma projector | Vision only | Not probed for audio |
| Installed MiniCPM-V 4.5 + resampler | Vision only | Not probed for audio |
| Installed Gemma 4 26B-A4B / 31B | Not audio variants; projectors absent | Not eligible |
| Acquired Qwen3-ASR 0.6B Q8 + matching Q8 `qwen3a` projector | Approved acquisition; exact hashes and audio metadata verified | Correct short-clip transcript on chat, multipart and SSE; cancellation/reuse/release verified |
| Gemma 4 E2B/E4B + correct mixed projector | Listed by b9957; absent locally | Not tested |
| Gemma 3n audio | Pinned loader explicitly skips its audio encoder | Unsupported in this pin |
| Audio generation / speech output | Not part of these ASR/chat input recipes | Not tested or claimed |
| Real-time chunked audio upload, timestamps, long clips, multiple audio items | Outside bounded trial | Not tested or claimed |

The Gemma 3n exception illustrates why even affirmative file metadata is not
sufficient without checking the pinned implementation.
[Pinned Gemma 3n skip](https://github.com/ggml-org/llama.cpp/blob/c4ae9a88f8884ee5a155c8349ace9ea31a58007f/tools/mtmd/clip.cpp#L3191).

## Positive trial and final decision

The exact launch recipe above ran under `OwnedProcessController` on the actual
Windows Comfy Python environment. The process had an assigned Windows Job and
one PID (73652) throughout all requests. `/props` reported audio true, vision and
video false. The offline 2.795-second WAV was the only audio input; no speakers,
microphone, external audio API or unrelated process was used.

The four initial probes and the post-cancellation request returned precisely:

```text
language English<asr_text>The blue notebook is beside the window.
```

The seven spoken words match exactly after separating the explicit ASR prefix.
The raw output is not just the sentence: the probe's strict `exact_words` field
is therefore false. This field is preserved rather than rewritten into a pass.
A future product adapter needs an explicit policy for language/control markers.

| Request | HTTP | Observed wall time |
| --- | ---: | ---: |
| Chat `input_audio` | 200 | 0.2211 s |
| Multipart transcription | 200 | 0.1254 s |
| Repeated chat in same process | 200 | 0.1082 s |
| Assembled chat SSE | 200 | 0.0970 s |
| Fresh chat after cancellation | 200 | 0.0796 s |

These are individual diagnostic measurements, not a throughput benchmark. The
probe used Requests connect/read timeouts (3/60 seconds), not an independently
enforced absolute deadline. Every completed response finished within 0.23 seconds;
this trial provides no adversarial slow-peer audio deadline evidence. The
first probe attempt completed nonstreaming requests but its local evidence script
failed on a valid nullable SSE content chunk. The script was repaired, the owned
server released, and the complete sequence rerun. The earlier artifact is retained;
there was no production-code workaround or discarded model-quality result.

Cancellation passed on the first short-clip attempt. Before DELETE, stream lookup
showed the exact conversation ID with `is_done=false` and zero buffered bytes;
slot 0 was processing task 60 with four of 49 prompt tokens processed. Exact-ID
DELETE returned 204 in 0.00082 seconds. Server logs recorded `cancel task` and slot
release about 52 ms later. The reader terminated, the slot became idle, the stream
lookup became empty, and a fresh request returned the same correct transcript.
The total reader-observation interval was 1.041 seconds. This demonstrates bounded
cancellation during this short audio processing request; it does not establish
an immediate interrupt guarantee inside every audio encoder kernel or long clip.

VRAM measurements were 14,127 MiB before launch, 16,443 MiB loaded, 16,555 MiB
observed after inference, and 14,129 MiB after release. Thus this trial added about
2.37 GiB at the highest sampled point. Sampling did not measure a continuous peak.
Normal owned shutdown completed in 0.249 seconds, without escalation or remaining
PIDs; the owned PID disappeared and port 18890 closed. Other host activity can
change the baseline, so the two-MiB residual is not evidence of a retained model.

**Decision:** the pinned combination is suitable for a bounded experimental
short-English-clip transcription path. Keep public audio support deferred because
this repository has no AUDIO input/output adapter, explicit marker policy, clip
bounds, multimodal workflow/UI contract or modality-specific lifecycle tests.
No public audio feature or runtime dependency was added. This investigation is
complete; any future audio feature must define and verify that separate contract.
Long clips, accents/languages, noisy audio, timestamps, multiple clips, live
microphone ingestion and generated speech remain untested. One synthetic spoken
sentence cannot establish recognition quality for those uses.

A narrow future implementation would add one explicit transcription operation:
accept a single mono PCM audio item capped at ten seconds, convert it in memory
to a complete WAV, require an explicitly selected audio-capable runtime/projector,
and submit at most 128 output tokens with the existing cancellation/ownership
machinery. Return transcript text plus preserved raw text/language metadata; strip
only the verified model-specific prefix, never arbitrary text. Keep microphone
capture, audio generation, multi-item input and automatic model downloads outside
that proposal. Before shipping, test malformed/silent/oversized input, missing
capability, prefix variants, slow-peer cancellation and downstream Comfy AUDIO
integration on the actual host. This is a scoped proposal, not an implemented
or validated public node contract.

A [sanitized result artifact](assets/2026-10-02-backlog/audio-results.json) records
artifact identities, exact transcripts, measured timings, cancellation outcomes
and resource/cleanup evidence.

Private reproducibility evidence: Project KB `evidence/2026-10-02/backlog/`, files
`audio-download.json`, `audio-fixture.wav`, `audio-fixture.json`,
`audio-positive.py`, `audio-positive.json` and `audio-positive-initial.json`.
The approved weights remain installed under `qwen3-asr-0.6b/` for future trials.

## Research verification and reproduction

The source cache was already present at
`~/Projects/References/github.com/ggml-org/llama.cpp`. Its working HEAD is newer;
all compatibility conclusions use `git show b9957:<path>` / `git grep ... b9957 --`.
`git rev-parse b9957^{commit}` resolved to the full commit recorded above.

The following reproduces the contained metadata inventory without mutating
product code or reading tensor data. Run from this repository; the selector
extension exists only inside this Python process:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python - <<'PY'
import json
from pathlib import Path
from models import gguf_metadata as gm

root = Path('/mnt/f/models/LLM/gguf').resolve()
original = gm._selected_metadata_key
gm._selected_metadata_key = lambda key: (
    original(key) or key == 'clip.has_audio_encoder' or key.startswith('clip.audio.')
)
scanned = count = 0
for path in sorted(root.rglob('*.gguf')):
    if not path.resolve().is_relative_to(root):
        raise RuntimeError('Out-of-root GGUF')
    meta = gm.read_gguf_metadata(path)
    scanned += meta.scanned_bytes
    count += 1
    print(json.dumps({'path': str(path.relative_to(root)), 'facts': {
        key: value for key, value in meta.values.items()
        if key in ('general.architecture', 'general.name', 'general.type',
                   'clip.projector_type', 'clip.has_vision_encoder',
                   'clip.has_audio_encoder') or key.startswith('clip.audio.')
    }}, sort_keys=True))
print(json.dumps({'files': count, 'header_bytes_scanned': scanned,
                  'tensor_payload_bytes_read': 0}))
PY
```

Additional research checks: `rg -n 'AUDIO|audio|input_audio' generation nodes
runtime/client.py runtime/streaming.py` found no public audio path; scoped pinned
source reads verified the route/parser/decoder/cancellation behavior above. HF API
inspection and two anonymous HEAD requests verified file metadata/access only.
No inference tests were run by the researcher. The parent's negative-probe JSON
and fixture metadata were read, and `wave`, `hashlib` and `struct` independently
verified the saved WAV's structure, hash and nonzero energy. The parent independently checked every final transcript, streamed equality,
post-cancellation response, process identity and cleanup result from the saved
positive-trial JSON. This documentation-only addition is reversible through git.
