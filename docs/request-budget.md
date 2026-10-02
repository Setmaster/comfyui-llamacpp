# Count the complete request before generation

**llama.cpp Request Budget** counts the request prepared by the same canonical
path as **llama.cpp Generate**. Connect the same prompt, connection, profile,
messages, image inputs, structured output, token bans and controls. Queue Request
Budget to count without generating a response. It does not open a generation
stream, generate reasoning, or produce partial text.

Counting can perform real model and media work. The node requires a running
target and uses the runtime's operation lease, request deadline and cancellation
checks. It sends `autoload=false`; refreshing discovery or a label does not count
a request or load a model. A managed router resolves the selected exact model
under its lease. An unloaded router target must be loaded explicitly first.

The three outputs are:

- **summary**: fit, overflow or unknown, with input count, requested output,
  effective context and remaining capacity.
- **budget_json**: a versioned envelope containing the budget, canonical payload
  SHA-256, runtime epoch when available, elapsed time and release evidence. It
  contains neither the prompt/media payload nor credentials.
- **budget**: a typed `LLAMACPP_REQUEST_BUDGET` observation for adapters.

`input_tokens` comes from `/v1/chat/completions/input_tokens`. `context_limit`
comes only from `/props.default_generation_settings.n_ctx`, the effective slot
context. Total server context and the model's training context are not substitutes.
Unknown values stay JSON `null`; an unavailable count endpoint is never a zero
count. Authentication, transport, malformed response and server errors remain
errors. A real count of zero is preserved as zero.

The budgeting policy is `input_tokens + max_tokens <= context_limit`.
**remaining** subtracts both input and the requested output allowance. Exactly
zero remaining fits; negative remaining overflows. This is an explicit local
policy. Context shifting and upstream generation limits can yield different
server behavior. No budget mode truncates history, summarizes text, changes the
prompt or reduces output allowance.

Request Budget defaults to **Report**, which exposes fit, overflow or unknown.
**Enforce** fails unless both facts are known and the allowance fits. Generate has
an optional **Budget Policy** control with **Off** as its compatible default.
**Report** counts immediately before generation and records the observation in
the generation result. **Enforce** stops before generation when the request
cannot be proved to fit. Both active policies count the final payload after
profile, history, media and exact model resolution.

A separate Request Budget node is a snapshot, not an approval for later
inference. Changing model, runtime, prompt, image, profile or output allowance
can invalidate it. Generate's own policy counts its current request again; it
does not accept another node's stale budget as permission to generate. Payload
hash and runtime epoch support comparison without retaining request content.

**Release After Count** requests terminal release for an owned direct runtime
or the selected owned router model. Inspect the envelope's `release` result.
An attached endpoint cannot be released by this node. Without requested release,
normal model reuse remains enabled. Terminal release is not proof of immediate
GPU-driver memory convergence.

Cancellation interrupts the local count response and closes its transport. The
upstream count API has no generation stream ID, so disconnecting does not prove
that upstream work stopped. Verified owned runtime/model release provides the
separate cessation evidence when requested and completed. DNS and connection
establishment retain the HTTP transport's existing native timeout limitations.

The complete-request endpoint includes template and media processing, unlike
the legacy text-only Token Count node. Counts are model/build dependent. Compare
with generation usage for the exact model and media type before assuming
multimodal counts have been validated for it. Legacy Token Count behavior is
unchanged.
