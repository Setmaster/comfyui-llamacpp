# Design: feature-frontier

Date: 2026-10-02

## Approach

Keep nodes thin and contracts bounded. Reuse canonical payload and lifecycle
coordination for messages, budgets, batch captions and ASR. Avoid copying the
coordinator. Native mapping proves independent generation but lacks group-level
cleanup on abort, so the aggregate operation must own its only release intent.

## Data model changes

Add immutable versioned messages, budget and aggregate values only where needed.
Old GenerationRequestSpec/result JSON remains round-trip compatible. ASR WAV
payloads remain ephemeral and never enter diagnostic results or workflow state.

## API / surface changes

Result serializer, message builder/append, explicit budget and caption operations,
local preset path plus inheritance choice, user-profile editor and short-clip
transcription. Append new primitive widgets and keep existing sockets stable.
Profile library endpoints retain the current read-only discovery contract.

## Migration notes

Existing inputs/defaults preserve prior payload and runtime behavior. No mandatory
workflow migration or new dependency. Native core string tools remain external
workflow requirements; examples must name their missing/null behavior.

## Risks

An outer lease plus inner release/wait deadlocks or blocks later admissions.
Only the group finalizer may request terminal release. A mutable preset path can
bypass validation during later router reload, so stage an owned validated
snapshot and include its content identity in configuration reuse decisions.
