# Explicit text messages

Generate's optional **Earlier Messages** input accepts a portable text-history
value from **llama.cpp Messages from JSON** or **llama.cpp Append Message**.
Generate sends those earlier turns before its current prompt. Nothing is added to
the history automatically, and the runtime keeps no conversation database for
these nodes. Prompt-prefix caching is independent of this explicit history.

## Build or import history

Append Message takes a role and exact text, plus optional earlier messages. It
returns a new history value and its **messages_json** representation. The input
history is unchanged. Connect another Append Message to extend that value, or
paste the JSON into Messages from JSON to restore a saved snapshot.

The JSON format is:

```json
{
  "schema_version": 1,
  "messages": [
    {"role": "user", "content": "Name a color."},
    {"role": "assistant", "content": "Blue."}
  ]
}
```

Only `system`, `user`, and `assistant` text roles are supported. Whitespace is
preserved. Content must be nonempty valid UTF-8 text; images, tool calls, nested
content objects, and extra fields are rejected. Duplicate JSON keys are rejected.
An imported snapshot is limited to 128 earlier messages, 262,144 characters per
message, and 1,048,576 serialized UTF-8 bytes. Those are data limits, not a promise
that a history fits a particular model's context window.

At most one system turn is allowed, at the beginning. If history includes that
turn, leave Generate's **System Prompt** empty and use a profile whose system
prompt is also empty. Conflicting system sources fail before runtime admission.
If history has no system turn, Generate's normal system/profile behavior applies.
Profile prefixes and suffixes wrap only the current prompt, not earlier messages.

Connected IMAGE inputs belong to the current user turn. Earlier messages are
text-only; an earlier response cannot make a previous image visible to a later
request. Reconnect the relevant image on the later Generate when it is needed.

## Few-shot draft and explicit refinement

Open [messages-few-shot.json](../example_workflows/messages-few-shot.json), choose
an installed text model, and configure the llama-server binary if necessary.
The graph performs two generations:

1. Messages from JSON supplies one system turn and a user/assistant example pair.
   The first Generate receives those three earlier turns and a current request
   from a core Text node.
2. Append Message retains that exact current request as a user turn. A second
   Append Message adds the first Generate's response as an assistant turn.
3. The second Generate receives the resulting five earlier turns and its own
   refinement request. Prompt Output shows the draft, refined response, portable
   history, and the final result JSON.

Appending only the assistant response would omit the user request that produced
it. This example deliberately connects the same Text value to the first Generate
and the user Append Message. Its Freeform profile does not transform that text.
If you add a transforming profile, explicitly retain the effective user text you
intend to represent in later history; Append Message does not reconstruct it.

Use the response socket for the assistant turn. Thinking is reported separately
and is not appended by this recipe. The graph uses strict generation failures;
an incomplete response is not silently promoted to a completed assistant turn.
If you deliberately choose Return Marked Partial elsewhere, inspect its result
state before appending the response. Append Message receives text and cannot
attest that its source was a completed generation.

The first Generate keeps the owned model available for refinement. The second
requests terminal release. If the graph fails or is interrupted before that
second request, its release cannot run; use ComfyUI's native **Unload Models** or
the pack's release control. This two-request example is not an aggregate cleanup
coordinator. Existing per-request cancellation and release behavior is unchanged.

Rerunning the graph rebuilds history from its explicit inputs and dependencies;
it does not append an earlier run implicitly. Saving the graph saves its literal
history JSON and wiring. To retain a generated conversation for a separate later
run, deliberately copy the full **messages_json** output into a Messages from
JSON node. To render a reviewed prompt without an LLM rerun, use the separate
[frozen-prompt workflow](graph-composition.md#review-once-then-render-the-saved-prompt).

## Result provenance

When Earlier Messages is connected, the typed result includes `messages_sha256`
and `message_count`. The hash identifies the canonical versioned earlier-history
snapshot, including its exact role/text values. The count includes a system turn
stored in that history. It excludes the current prompt and any system prompt
supplied separately by Generate or a profile. It is not a hash of the whole HTTP
request, images, or model settings.

An explicitly connected empty history has a hash and count zero. When no history
is supplied, the existing request/result JSON shape is preserved without these
additional provenance fields. The result does not embed the earlier message
bodies; export **messages_json** explicitly when you need the reusable snapshot.
