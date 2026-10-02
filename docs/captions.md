# Independent image captions

**llama.cpp Caption Batch** takes one Comfy IMAGE tensor containing 1 to 32
images. It sends each image in a separate request, in tensor order. Each request
uses that item's prompt and seed, with the shared model, profile, system prompt,
sampling, and structured-output settings. No earlier caption becomes a later
item's conversation history.

Generate's existing **Full Batch** setting still sends multiple images together
in one request. Use Caption Batch when each image needs its own response.

## Set the item inputs

**Prompt** and **Seed** are scalar defaults for every item. The optional JSON
fields let you make the mapping explicit:

| Input | Blank value | Explicit value |
| --- | --- | --- |
| Item IDs JSON | Zero-based text IDs: `"0"`, `"1"`, and so on | An exact-length list of unique, nonempty text IDs |
| Per-item Prompts JSON | Broadcast Prompt | One JSON string to broadcast, or an exact-length list of strings |
| Per-item Seeds JSON | Broadcast Seed | One JSON integer to broadcast, or an exact-length list of integers |

For two images, for example:

```text
Item IDs JSON:          ["front", "back"]
Per-item Prompts JSON:  ["Describe the visible object.", "Describe the visible colors."]
Per-item Seeds JSON:    [101, 202]
```

A one-entry list is valid only for a one-image batch. Lists never repeat their
last value to fill missing items. JSON `null` is invalid; leave a field blank to
use its default. Seeds must be integers from 0 through 2,147,483,647. IDs are at
most 128 characters and are preserved exactly. The node does not infer filenames
from an IMAGE tensor. Supply filenames as IDs explicitly if you need that mapping.

Each JSON input is limited to 1 MiB of UTF-8 text. Duplicate object keys,
nonfinite numbers, unsupported nested values, and invalid UTF-8 text fail
validation. Item count and alignment are checked before transferring images to
the CPU. Image preparation slices one frame before CPU conversion; the combined
encoded image data is limited to 32 MiB.

## Inspect completion and release

The **captions** output is a native Comfy list of STRING values, in the same order
as the input images. **result_json** is one group record with aligned item rows.
Every row retains its ID, seed, and state. Completed rows include generated
response and thinking, exact model, usage, and stream-completion evidence. The
group has separate release metadata and warnings. Result JSON is limited to
8 MiB, including all rows.

The envelope uses `schema_version: 1` and `operation: "captions"`. Its `rows`
array stores each row's `item_id`, `seed`, `state`, `response`, `thinking`,
`result`, and `error`. The nested `result` retains the canonical generation
metadata when available. Unattempted rows have empty text and null result/error.
The envelope's `release` describes the final group operation separately from
the individual generation results.

The default **Raise Error** policy fails the group if an item fails. It exposes
no successful caption-list output from that run. **Return Marked Partial** is an
explicit alternative: every original ID remains represented as `complete`,
`failed`, or `not_attempted`. Only completed rows produce captions; failed and
unattempted positions contain empty strings. A failed row may retain incomplete
response text in its metadata. Inspect row states, group state, release, and
warnings before using or exporting marked partials. Never drop blank entries and then zip the
remaining captions with the original IDs.

All items share one runtime admission and one total timeout, including image
preparation and terminal release waiting. Cancellation stops further work; its
error category and group state distinguish it from normal completion. The
coordinator releases its operation resources after failure or interruption.
Comfy's own queue interrupt can abort node execution before outputs are exposed.

**Release After Generation** asks the owned runtime to release only after the
group finishes or stops. When enabled, successful outputs wait for terminal
release evidence. Attached endpoints cannot provide this owned-release option.
When disabled, the model can remain loaded for later work. Release metadata
describes the runtime operation; it is not a claim that driver GPU memory was
independently measured.

## Two-image recipe

Open [caption-batch-aligned.json](../example_workflows/caption-batch-aligned.json).
Choose an installed vision model and matching projector in Start Server, then
select one still image in each Load Image node. Use matching dimensions: the
native ImageBatch node resizes the second image when dimensions differ.

The graph batches image 1 before image 2, assigns IDs `first` and `second`, and
uses seeds 101 and 202. It keeps strict failures and terminal release enabled.
One Prompt Output receives the native caption list; the other shows the complete
group JSON for checking alignment and release. If a selected file expands into
multiple frames, the exact two-item ID list fails rather than silently assigning
the wrong IDs.

The recipe does not write caption sidecar files. Export the group JSON or pass
the aligned captions to a downstream writer deliberately. A filename-based
writer should take the explicit IDs and check completion states first.

Native list mapping can also run Generate repeatedly after an IMAGE batch is
split into separate list items. That composition does not give the independent
Generate calls a shared group timeout or final cleanup boundary, and native
mapping repeats the last value of a shorter input list. Caption Batch provides
the explicit alignment and group lifecycle described above.
