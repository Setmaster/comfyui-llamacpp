# 0.4 Canonical Generate User Acceptance

Use this after automated, browser, runtime, GPU, and CI validation is complete.
It is the maintainer's final hands-on gate before `dev` is blessed for merge.
The accepted 0.3 checklist remains available in
[user-acceptance.md](user-acceptance.md).

## Candidate identity

- [ ] The installed clone is clean on `dev` at the candidate commit recorded in
  the latest validation report linked from the README. Earlier reports describe
  only their named revisions.
- [ ] Startup reports package `0.4.0` and 25 llama.cpp nodes.
- [ ] `master` and tag `0.3.0` still resolve to the accepted hashes recorded in
  the validation report.
- [ ] No unexpected ComfyUI or llama-server process was already running before
  the test.

## First-run and graph UX

- [ ] Open each canonical template from the workflow browser without missing
  nodes, warnings, or shifted widget values.
- [ ] Create Generate and Task Profile from search in classic and Nodes 2.0.
- [ ] Default sampling hides the expert samplers. Custom reveals all seven.
- [ ] Image Inputs shows exactly 0, 1, and 10 image sockets when selected.
- [ ] Duplicate, save, reload, and reopen both nodes. No live, status, selector,
  Refresh, Update, or Stop widget is serialized.
- [ ] App Mode shows the intended prompt and primary controls, queues normally,
  and updates bounded, read-only Generation Status and Live Response fields.
- [ ] The jobs/history APIs retain the terminal native text output. Treat a
  missing central text preview in affected Comfy frontend builds as the
  documented upstream parser limitation, not loss of the generated response.

## Direct text and strict behavior

- [ ] Generate through a Start Server URL connection.
- [ ] Generate again with both connection inputs absent and confirm it reuses the
  current managed runtime.
- [ ] Default sampling and Thinking Auto produce a normal response.
- [ ] Custom sampling and explicit Thinking Off or On reach the server.
- [ ] A bad URL, bad model, invalid request, and impossible JSON response raise
  clear categorized errors instead of returning an error string.
- [ ] Default partial policy withholds sockets. Explicit marked-partial mode
  returns a typed non-complete result when partial text exists.

## Live preview and cancellation

- [ ] Response and thinking tails update while a long generation runs and remain
  bounded under long output. Truncated fields show the tail notice in graph and
  App Mode; the next short run clears it, and native history retains the response.
- [ ] Current llama.cpp exposes **Stop generation**. It stops only that execution
  and the final status does not claim upstream confirmation unless DELETE was
  confirmed.
- [ ] An endpoint without exact stream support exposes **Stop Comfy job** and
  performs prompt-targeted interruption.
- [ ] Reload the browser during a generation. The active state restores once and
  stale events do not replace the newer execution.
- [ ] Two Generate nodes and a mapped/list execution keep their previews and Stop
  actions attached to the correct displayed execution.

## Vision and structured output

- [ ] Leave Vision Projector on `(auto)` for one known compatible VLM bundle.
  Confirm Server Status names the auto-selected relative projector, `/props`
  reports vision, and the response refers to visible evidence from the image.
- [ ] Select the same projector explicitly. Confirm the same server PID is
  reused and Status changes only the selection provenance to explicit.
- [ ] Select `(none - text only)`. Confirm text generation works, no projector
  is loaded, and a connected image fails locally with the specific
  capability-unsupported message.
- [ ] Put equivalent Q8_0 and F16 projector quantizations beside a test model.
  Confirm `(auto)` selects Q8_0 deterministically.
- [ ] Present two distinct compatible projector identities. Confirm `(auto)`
  fails before replacing a healthy current server and asks for an explicit
  selection.
- [ ] Confirm a mixed-size model folder never pairs a projector whose effective
  embedding interface belongs to the other model size.
- [ ] Test a full Comfy IMAGE batch and confirm every intended frame is sent.
- [ ] Generate one JSON object and one nested JSON Schema result, then parse each
  response as JSON.
- [ ] Connect Token Ban and verify the request still completes through the same
  canonical node.

## Passive discovery and profiles

- [ ] Refresh managed direct and router models without loading an unloaded model
  or allocating new LLM VRAM.
- [ ] Save a nonexistent model value, Refresh, and confirm it remains visible as
  Missing.
- [ ] Confirm unloaded facts appear as Unknown rather than unsupported.
- [ ] Create a current-user `profiles.json`, Refresh, select an entry, and verify
  that selection alone does not modify the saved workflow.
- [ ] Press **Update Saved Snapshot**, undo, redo, save, and reopen.
- [ ] Change or remove the local entry. The node reports Changed or Missing while
  executing the saved snapshot identically.

## Scoped and global lifecycle

- [ ] Direct Release After Generation withholds output until the owned process
  tree is gone and driver memory converges.
- [ ] With two overlapping direct generations, release waits for both and stops
  the process once.
- [ ] In router mode, release after model A leaves the router and resident model B
  intact.
- [ ] New model B work remains admissible while model A scoped cleanup is pending.
- [ ] An attached endpoint rejects Release After Generation before submitting a
  prompt and remains untouched.
- [ ] Comfy's native **Unload Models** still releases the globally owned runtime.
- [ ] Explicit Stop, Release, Load, and Unload nodes retain their 0.3 behavior.
- [ ] After terminal LLM release, run the downstream diffusion workload without
  restarting ComfyUI.

## Graph composition, messages and budgets

- [ ] Connect Generate's typed result to **Result JSON** and inspect completion,
  usage, errors and release evidence through Prompt Output.
- [ ] Run the structured-field and protected-literal composition examples, then
  render a saved prompt through the separate core-only frozen-render workflow.
  Editing and rendering the frozen prompt must not start llama-server.
- [ ] Append explicit user and assistant turns, export/import their JSON, and
  generate a continuation. Repeat with a changed prompt and verify only the
  supplied history is used.
- [ ] Run **Request Budget** with Report: inspect the complete-request input
  count and output allowance without generating text. Generate with Enforce
  accepts a fitting request and rejects overflow before generation. Unknown
  facts remain Unknown; no text is silently truncated.

## Router presets and aligned captions

- [ ] Start a router with a local INI preset. Check exact model identities and
  effective settings for Override and Inherit. Edit the preset and requeue Start
  to verify the change; an invalid replacement preserves the healthy runtime.
- [ ] Load two exact router IDs. Count, generate and release one while verifying
  the other remains loaded and the router PID is unchanged.
- [ ] Caption a two-image batch with distinct IDs. Verify independent captions,
  order, IDs and item states in `result_json`. Check a failed/cancelled item
  under both strict and marked-partial policy, preserving every item position.
- [ ] Enable caption terminal release and verify cleanup completes once for the
  group before outputs become available.

## Profile editor and experimental transcription

- [ ] Create, edit, import and export a user profile. A library save must not
  change the workflow snapshot; **Update Saved Snapshot** is explicit and
  undoable. A conflicting save reports a revision conflict and preserves the
  newer library.
- [ ] With the [documented installed ASR pair](audio-transcription.md), connect
  Load Audio to Transcribe using a nonempty mono 16 kHz clip of at most ten
  seconds. Inspect transcript, raw output, prefix status and result metadata.
- [ ] Reject an invalid audio shape/rate or unsupported pair before submission.
  Verify cancellation and optional terminal release. Skip this section only
  with an explicit record that the supported local fixture was unavailable.

## Compatibility and closeout

- [ ] Open representative 0.2.1 and 0.3 workflows with all 17 legacy nodes.
- [ ] Queue Basic, ADV, and ADV++ smoke workflows and confirm their existing
  response, thinking, and success outputs remain unchanged.
- [ ] Switch back to `0.3.0`, restart, and confirm the legacy workflow still
  loads. All eight candidate-only node types become unavailable: Generate,
  Task Profile, Result JSON, Append Message, Messages from JSON, Request Budget,
  Caption Batch and Transcribe.
- [ ] Restore the exact candidate, restart, and confirm the installed clone is
  clean before blessing the merge.
