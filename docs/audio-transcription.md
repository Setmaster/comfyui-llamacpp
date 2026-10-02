# Experimental short-clip transcription

This candidate feature converts one short voice clip into text for a downstream
Comfy workflow. It targets the previously verified **Qwen3-ASR 0.6B Q8_0 model
with its matching Q8_0 audio projector**. The public node has passed native
Windows Comfy AUDIO wiring and transcription checks with this pair. The
[feature validation report](validation-2026-10-features.md) records the tested
scope; this is not a general accuracy benchmark.

## Input and runtime requirements

Use **Start llama.cpp Server** in direct mode with both files selected explicitly:

- Model: `qwen3-asr-0.6b/Qwen3-ASR-0.6B-Q8_0.gguf`.
- Vision Projector: `qwen3-asr-0.6b/mmproj-Qwen3-ASR-0.6B-Q8_0.gguf`.

The Start node's existing projector label also supplies the audio projector. It
does not make a vision projector audio-capable. The transcription operation
requires a positively owned direct server, the exact approved file contents, and
an affirmative audio capability response. Attached servers, router mode, unknown
capability and other model/projector variants are outside this first contract.
Nothing is downloaded or started automatically by Transcribe.

Connect **Load Audio** to **llama.cpp Transcribe (Experimental)**. Connect Start
Server's URL output to Transcribe's `server_url` input to establish graph order;
an empty URL uses the currently owned runtime. Select the supported pair in the
Transcribe node. Set `request_timeout` for the whole request and optionally enable
`release_after_generation` to wait for terminal owned cleanup before returning.

Accepted Comfy AUDIO values contain floating-point PCM waveform data with shape
`[1, 1, samples]` and integer `sample_rate=16000`. There must be between one and
160,000 samples, at most ten seconds. Samples must be finite and within `[-1, 1]`.
Stereo, multiple items, other sample rates, integer waveform arrays, empty clips
and out-of-range samples are rejected. This node does not silently downmix,
resample, normalize or trim input. Exact silence after PCM16 conversion is also
rejected; no speech-detection threshold is claimed.

The waveform is converted to a complete mono PCM16 WAV in memory, bounded to
320,044 bytes, then sent as inline `input_audio` through the shared streaming chat
transport. No temporary audio file, URL fetch, playback or microphone access is
used. Output is limited to 128 tokens with temperature zero. There are no prompt,
profile, messages, sampler or audio-generation controls on this operation.

## Outputs and prefix handling

| Output | Meaning |
| --- | --- |
| `transcript` | Text after removing only the exact verified leading prefix `language English<asr_text>`. Otherwise the complete raw response. |
| `raw` | The model response unchanged, including all language/control text and whitespace. |
| `language` | `English` for that exact prefix; empty when the prefix is absent or unrecognized. |
| `result` | Canonical generation result with raw response, completion, model, timing, usage, operation provenance and release evidence. |
| `metadata_json` | Audio rate, count, duration, WAV and approved artifact hashes, language, prefix status and parsing warnings. No waveform or local path. |

Unknown prefixes are not guessed or stripped. Their status is
`unrecognized_or_absent`, with a warning that transcript preserves the raw
response. The known prefix has status `verified_english`; the suffix is preserved
exactly. Empty output or the verified prefix without transcript text is a protocol
failure. Partial, cancelled and token-limit-truncated responses must not be
presented as completed transcripts.

Connect `transcript` to the ordinary text input of Generate or another graph
operation. Transcription runs against the ASR runtime; a downstream text model
may require an explicit release/start sequence. A single direct server cannot
quietly switch model families between these nodes.

## Identity, timing and evidence

The approved files are checked by size and SHA-256, independent of their names:

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| Model | 804,749,248 | `bca259818b50ca7c4c05e9bdb35a5dc04fa039653a6d6f3f0f331f96f6aa1971` |
| Audio projector | 214,392,480 | `41a342b5e4c514e968cb756de6cd1b7be39eff43c44c57a2ef5fc6522e36603d` |

The first identity check reads about 1.02 GB of already installed local files in
bounded chunks. Subsequent checks reuse a small in-process cache only while the
resolved path and file identity/stat signature remain unchanged. Cancellation and
the request deadline apply during verification as well as generation. File hashes
prove the configured files; managed ownership and the admitted runtime epoch must
separately bind them to the active launch.

The [recorded compatibility trial](research/llamacpp-audio-compatibility-2026-10-02.md)
used llama.cpp b9957 and one synthetic English sentence. It verified short-clip
chat/SSE transcription, cancellation, reuse and owned release for this pair.
It did not establish accuracy for long, noisy, accented or multilingual speech.
Audio generation, timestamps, multiple clips and incremental microphone input
remain outside this feature.

Native acceptance must still verify the actual Load Audio wiring, transcript
outputs, downstream text workflow, Stop, deadline, missing-capability rejection
and terminal release with the integrated candidate. Offline preparation tests do
not substitute for that host evidence.
