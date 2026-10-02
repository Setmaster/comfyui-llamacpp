# Design

Keep UI tail decoration inside the existing transient live-state adapter. Leave
backend payloads and native history untouched. Keep PNG encoding stable while
moving shape inspection and bounded tensor selection ahead of CPU conversion.

Use deterministic fixtures and exported profile snapshots for the benchmark,
with existing dependencies and the installed Windows Qwen3-VL/llama.cpp runtime.
Use pinned upstream source plus bounded installed GGUF headers for audio; do not
infer audio capability from architecture or a vision projector filename.

Parent coordinates all GPU/runtime mutations; independent workers own separate
UI, image helper, profile benchmark and audio research files. No release promotion.

The fixed profile follow-up uses three new deterministic images and the already
installed MiniCPM-V4.5 as a second model. Scores are finalized blind to model/arm
before aggregation. Failed quality gates mean defer, with no automatic extra
rounds or profile-content changes. Audio research uses the explicitly approved
pinned Qwen3-ASR pair under the existing owned process controller; the public
Comfy AUDIO contract remains a separately scoped proposal.
