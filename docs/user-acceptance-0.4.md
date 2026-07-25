# 0.4 Canonical Generate User Acceptance

Use this after automated, browser, runtime, GPU, and CI validation is complete.
It is the maintainer's final hands-on gate before `dev` is blessed for merge.
The accepted 0.3 checklist remains available in
[user-acceptance.md](user-acceptance.md).

## Candidate identity

- [ ] The installed clone is clean on `dev` at the candidate commit recorded in
  [validation-0.4.md](validation-0.4.md).
- [ ] Startup reports package `0.4.0` and 19 llama.cpp nodes.
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
  bounded under long output.
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

## Compatibility and closeout

- [ ] Open representative 0.2.1 and 0.3 workflows with all 17 legacy nodes.
- [ ] Queue Basic, ADV, and ADV++ smoke workflows and confirm their existing
  response, thinking, and success outputs remain unchanged.
- [ ] Switch back to `0.3.0`, restart, and confirm the legacy workflow still
  loads. Generate and Task Profile alone should show as missing.
- [ ] Restore the exact candidate, restart, and confirm the installed clone is
  clean before blessing the merge.
