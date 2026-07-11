# 0.3 user acceptance checklist

Run this checklist on the final `dev` revision before blessing a merge to
`master`. Record the commit, ComfyUI version, frontend version, llama.cpp build,
OS, GPU, driver, and model names.

## 1. Upgrade and workflow compatibility

- [ ] Switch the installed custom node checkout to final `origin/dev`.
- [ ] Install requirements with ComfyUI's Python and restart ComfyUI.
- [ ] On Linux, confirm the host provides `pidfd_open` and
      `waitid(P_PIDFD)` (normally Linux 5.4 or newer), or confirm startup fails
      before spawning a server with the documented capability error.
- [ ] Startup reports version 0.3.0 and 17 nodes.
- [ ] Open representative 0.2.1 workflows without missing legacy nodes.
- [ ] Verify old model, prompt, sampling, and Boolean widget values retained
      their meaning.
- [ ] Save and reload ADV and ADV++ nodes with image counts 0, 1, and 10.
- [ ] Confirm the same named image sockets remain connected.

## 2. Direct text and utility nodes

- [ ] Start a direct text model with the final current llama-server build.
- [ ] Server Status shows `owned=true`, `mode=direct`, the expected binary
      identity, and the expected process ownership primitive.
- [ ] Basic Prompt returns response text and a true success output.
- [ ] Token Count returns a plausible nonzero count.
- [ ] Model Info returns the selected model and a nonzero context length.
- [ ] Prompt Output displays the full response after switching browser tabs.

## 3. Native and explicit direct release

- [ ] Record the owned PID and driver-visible VRAM after generation.
- [ ] Use Comfy **Unload Models**.
- [ ] Confirm the old PID and every owned descendant are gone.
- [ ] Confirm runtime status is `mode=none`, `owned=false`, `lifecycle=idle`.
- [ ] Confirm VRAM converges near the expected baseline.
- [ ] Repeat using **Release llama.cpp VRAM**.
- [ ] Repeat using **Stop llama.cpp Server**.

## 4. Router barriers

- [ ] Start router mode and list its exact model IDs.
- [ ] Explicitly load a model and confirm the node waits for `loaded` or
      `sleeping`.
- [ ] Generate with that model.
- [ ] Explicitly unload it and confirm the node waits for a nonresident state.
- [ ] Load at least one model again, then use Comfy **Unload Models**.
- [ ] Confirm all resident models become nonresident while the router PID
      remains alive.
- [ ] Stop the router explicitly and confirm its owned process tree disappears.

## 5. Generation/release concurrency

- [ ] Start a long managed generation.
- [ ] Invoke native unload while it streams.
- [ ] Confirm release is reported as pending or deferred and the process remains
      until the generation exits.
- [ ] Interrupt or complete the generation.
- [ ] Confirm pending release runs immediately afterward.
- [ ] Confirm start, stop, load, and unload races never leave status claiming a
      live runtime when no process exists.

## 6. VLM

- [ ] Start a known VLM with its exact matching projector.
- [ ] Describe one image through ADV Prompt.
- [ ] Run ADV++ Image2Prompt and verify the backend template applies.
- [ ] Test `image_amount=0`, one image, and ten visible sockets.
- [ ] Test `include_image_batch` with a small batch and verify every image is
      represented in the request outcome.

## 7. Structured generation and token bans

- [ ] Generate a JSON object with `json_object` mode.
- [ ] Generate against a nested JSON Schema and validate the returned JSON.
- [ ] Test a small GBNF grammar supported by the chosen model/server.
- [ ] Apply a token ban and confirm it changes or constrains the output.
- [ ] Test a stop sequence containing a comma by entering it as a JSON array.

## 8. Ownership boundaries

- [ ] Start an unrelated second process whose executable name is
      `llama-server`.
- [ ] Stop this pack's owned server and confirm the unrelated process survives.
- [ ] Attach a prompt node to an externally started local server.
- [ ] Use native Comfy unload and confirm the attached server remains alive.
- [ ] On Windows, require `windows_job_assigned=true` and
      `descendant_fallback=false` for abrupt-owner cleanup acceptance.
- [ ] Exit Comfy normally and confirm the owned tree disappears on every
      supported platform.
- [ ] On Linux or Windows with an assigned Job Object, repeat with abrupt owner
      termination and confirm the owned tree disappears.

## 9. Full GPU handoff

- [ ] Run a representative diffusion workflow and record peak and idle VRAM.
- [ ] Start llama.cpp with `unload_comfy_models_before_start=true`.
- [ ] Confirm the LLM allocates and generates without restarting ComfyUI.
- [ ] Release the LLM through native unload and wait for terminal state.
- [ ] Confirm driver-visible memory converges.
- [ ] Run the same diffusion workflow again without restarting ComfyUI.
- [ ] Treat successful functional reallocation without OOM as the decisive pass.

## Acceptance result

- Final dev commit:
- Result: PASS / CONDITIONAL / FAIL
- Failed checks:
- Logs or screenshots:
- Remaining known limitations:
