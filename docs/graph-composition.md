# Compose generation with native ComfyUI nodes

The 0.4 candidate includes **llama.cpp Result JSON** and three graph recipes. They
reuse ComfyUI's text utilities and keep generation separate from rendering a
reviewed prompt. Choose installed models after importing an example; the saved
model names are portable placeholders.

## Inspect the generation result

Connect Generate's **result** socket to **llama.cpp Result JSON**, then connect
**result_json** to **llama.cpp Prompt Output** with **Plaintext** disabled.

Result JSON serializes the existing versioned `GenerationResult` contract. Its
JSON contains the complete response and separately reported thinking, state,
model/profile identity, seed, usage, finish evidence, warnings, timing, errors,
and release evidence. It uses the same bounded serializer as the result contract;
it does not truncate the text to the live preview tail or change a partial result
into a complete one. It accepts a typed result from Generate, not arbitrary JSON.

The adapter neither generates text nor requests runtime cleanup. A release with
`status: not_requested` still means no release was requested. Terminal process or
model cleanup does not prove GPU-driver memory has converged; inspect
`driver_memory_verified` and the original release evidence. With Generate's
default strict policy, a failed generation raises before producing downstream
outputs. **Return Marked Partial** can expose incomplete or cancelled text; its
JSON remains explicitly marked and must be reviewed before reuse.

## Extract a structured prompt field

Open [graph-structured-field.json](../example_workflows/graph-structured-field.json).
The graph connects a JSON Schema to Generate, then sends the response STRING to
core **Extract Text from JSON** (`JsonExtractString`) with key `positive_prompt`.
The extracted STRING goes to Prompt Output. A separate branch serializes the
typed result for inspection.

Connect the extracted STRING to a downstream text input, such as the positive
text of a CLIP Text Encode node. The response is already JSON text, so no custom
Python or duplicate parser node is needed. Keep strict generation enabled for
this recipe and review the extracted field before adopting it for rendering.

Core extraction reads a top-level key from the first decodable JSON object. A
missing key, JSON `null`, or no decodable object returns an empty STRING. It does
not validate a schema, assert that a prompt is nonempty, select a nested path, or
verify model facts. Other value types are converted with Python `str`, so the
recipe requests a string field. Generate validates completed JSON syntax; that
is distinct from independent local validation of every JSON Schema rule.

## Append protected literal text after generation

Open
[graph-protected-literal-draft.json](../example_workflows/graph-protected-literal-draft.json).
Generate writes the descriptive part. Core **Text (Multiline)** stores the literal
`Mira Vale, (blue coat:1.2), example_tag`, and **Concatenate Text** appends it with
`, ` as the delimiter. The literal is not connected to the LLM prompt.

Concatenation preserves the literal's characters, including names, tags, and
weighting syntax. It does not check whether a name or tag is useful for the image
model, remove duplicates, or guarantee that the generated description is correct.
Profile prefixes and suffixes modify the input sent to the LLM; they do not offer
this after-generation preservation guarantee.

## Review once, then render the saved prompt

These are two separate queue operations and two separate workflow files:

1. Queue the draft workflow and inspect the full final text in Prompt Output.
   Edit or reject it as needed. The example requests terminal managed release
   after generation; check the result/status before beginning a GPU render.
2. Open [graph-frozen-render.json](../example_workflows/graph-frozen-render.json).
   Paste the reviewed final prompt into **Frozen prompt**, edit it, and save this
   workflow. The supplied text is an illustrative starting value, not an output
   automatically adopted from your last generation.
3. Select an installed standard SD1.5 or SDXL checkpoint compatible with the
   example's core checkpoint loader, CLIP encoder, latent image, sampler, and VAE
   decoder. Other model families may require their own render nodes.
4. Queue the render workflow. Adjust its diffusion seed or other render settings
   and queue again. Its saved STRING is the prompt source; this graph contains no
   Generate, Start, or other llama.cpp node and makes no LLM request.

The saved text is a deliberate queue boundary, not a cache trick or paused
approval node. Keeping a Generate branch in a render graph can still run it,
especially because Generate is an output node. Changing a preview widget or
disconnecting one response link is not equivalent to removing that generation
branch. The separate frozen workflow makes the boundary explicit and portable.

## Per-image caption mapping and its cleanup boundary

The candidate's [Caption Batch node](captions.md) provides bounded independent
requests with exact alignment and one group lifecycle. The native composition
below explains why ordinary list mapping has different cleanup behavior.

Full Batch means all selected frames are sent in **one** multimodal request. It
does not mean one caption request per frame. Native Comfy list mapping can instead
invoke ordinary nodes once per list item when an upstream node emits a Comfy list
of individual IMAGE values. A batched IMAGE tensor alone is not that list.

Source analysis of installed ComfyUI commit
`8d534945ebd53cff61e8def81757c6a6c1b9cf2d` establishes the following behavior in
[`execution.py:251-327`](https://github.com/Comfy-Org/ComfyUI/blob/8d534945ebd53cff61e8def81757c6a6c1b9cf2d/execution.py#L251-L327):

- A synchronous node without `INPUT_IS_LIST` runs once for each mapped item.
  A shared one-element prompt is broadcast across the images.
- A shorter multi-item input list also repeats its last item. Native mapping
  therefore does not enforce exact image/prompt/filename alignment.
- An uncaught exception or Comfy interruption stops the synchronous mapping loop.
  A downstream release node cannot be the only cleanup guarantee on that path.

Generate and the existing Release Runtime node are ordinary mapped nodes. Linking
a result list to Release Runtime's trigger maps that release node per item; it
does not create a single batch finalizer. Enabling Generate's release for each
item also requests release at each item, rather than retaining the model until
the group finishes. Disabling it allows reuse but does not add cleanup after an
uncaught group failure. These are source-backed lifecycle limits, not evidence
that native per-image generation is unavailable.

A supported caption-group workflow needs bounded item count, ordered IDs,
scalar-or-exact-length prompt/seed pairing, explicit per-item outcomes, and one
terminal cleanup path on success, failure, and cancellation. Those requirements
belong to the caption pipeline; this document does not present a simple downstream
release connection as satisfying them. Never infer source filenames from an
anonymous IMAGE tensor.

The source-only feasibility assessment did not run third-party reference code or
claim native inference results. Native validation should exercise several distinct
images with a shared prompt and exact-length prompts, then a middle-item failure
and cancellation. Check request order, result association, one terminal release,
and subsequent GPU allocation before calling a caption recipe supported.
