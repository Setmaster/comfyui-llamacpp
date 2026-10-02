# Spec: feature-frontier

Date: 2026-10-02

## Requirements

1. Serialize the complete typed generation result and provide native JSON,
   protected-literal and frozen-prompt workflow recipes without another parser.
2. Select local router presets, explicitly inherit or override common settings,
   honor same-path edits, reject implicit acquisition and preserve a healthy
   runtime if the replacement is invalid.
3. Accept bounded versioned system/user/assistant text history as explicit graph
   data. Preserve bytes/order, reject conflicting system sources, retain current
   image placement and leave old payloads unchanged when history is absent.
4. Count the exact final request with authoritative per-slot context provenance.
   Unknown is not zero; budget policy must not silently rewrite a request.
5. Emit ordered per-image results with validated IDs/prompt/seed pairing, bounded
   progress and one group release after success, failure or cancellation.
6. Edit/import/export current-user profiles atomically with optimistic conflict
   detection. Library writes never silently replace portable workflow snapshots.
7. Expose experimental mono PCM AUDIO transcription, at most ten seconds and
   128 output tokens, using the approved installed ASR pair. Preserve raw text and
   parse only verified control prefixes; canonical lifecycle semantics apply.

## Scenarios

The implementation must pass each proof paragraph in the feature-frontier report
and the matching Linear issue. Include malformed/oversized input, conflict and
failure/cancel paths, old saved workflows, direct/router exact targeting, native
Windows models and applicable classic/Nodes 2.0/App Mode surfaces.

## Resolved coordination questions

Messages, budgeting, caption groups and transcription share the canonical
admission, payload, deadline and finalization path. Caption items retain one
lease and client, with one terminal group release; native mapping alone does not
provide failure cleanup. Count cancellation uses request-local response guards,
including bounded Windows reads, with independent current/minimum/native proof.
Client transport cancellation alone does not prove upstream work stopped;
terminal owned release supplies that separate lifecycle evidence.
