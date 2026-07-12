# Post-0.3 Migration Guide

The post-0.3 line is an additive generation and lifecycle update. It keeps all
17 nodes and saved-workflow contracts from 0.3, then adds **llama.cpp Generate**
and **llama.cpp Task Profile**. There is no automatic workflow rewrite and no
legacy prompt node is deprecated in this release.

## What remains unchanged

- Every 0.2.1 and 0.3 node class ID, display name, function, socket, output
  order, default, and primitive widget prefix.
- Basic Prompt, ADV Prompt, and ADV++ Prompt execution and partial-result
  conventions.
- Start node outputs. Their existing `server_url` strings remain valid.
- Explicit Stop, Release, List, Load, and Unload nodes.
- Native Comfy `Unload Models` integration for positively owned runtimes.
- The external `llama-server` process boundary and local-only product scope.

The immutable `0.3.0` tag remains the stable rollback point.

## Recommended path for new workflows

Use one Generate node for freeform text, prompt generation, image understanding,
and constrained output. It replaces the need to choose between Basic, ADV, and
ADV++ when building a new graph, but it does not remove them.

Connect it in one of three ways:

1. Connect a typed **llama.cpp Connection**.
2. Convert the advanced `Server URL` widget to an input and connect a Start
   node's existing URL output.
3. Leave both absent and use the currently owned managed runtime.

Supplying both Connection and a nonempty Server URL is an error. In router mode,
select one exact model ID. Managed Refresh is passive and retains a missing
saved value instead of replacing it.

When current router status records expose their selected GGUF through launch
arguments or preset metadata, Generate resolves a local selection under the
active router root and compares the complete normalized paths. A matching
basename or suffix in another directory does not pass. A directory-level router
ID that actually points at another quantization is rejected before prompt
submission. Exact canonical live router IDs remain authoritative. Older routers
without target evidence retain ID-only compatibility, so keep one base GGUF in
each portable router bundle.

## Behavior differences to expect

Generate is intentionally stricter than the legacy prompt surface:

- Total failures raise and prevent downstream execution.
- Partial output raises by default. It reaches sockets only with the explicit
  `return_marked_partial` policy and remains marked in the typed result.
- JSON object and JSON Schema results receive final JSON syntax validation.
- Default sampling omits all custom sampler keys. Custom sends the complete
  expert group.
- Thinking Auto omits an override. Off and On send an explicit Boolean.
- Live preview is bounded and browser-only. It is never serialized into the
  workflow.
- Supported current llama.cpp servers expose exact `Stop generation` control.
  Other endpoints expose the truthful `Stop Comfy job` fallback.
- Canonical workflows can name only `LLAMACPP_API_KEY` or a namespaced
  `LLAMACPP_API_KEY_<UPPERCASE_SUFFIX>` credential. Authenticated plain HTTP is
  limited to loopback. Authenticated non-loopback use additionally requires
  HTTPS, certificate verification, and an exact server-side origin-to-key entry
  in `LLAMACPP_REMOTE_AUTH_BINDINGS`. See the Authentication and TLS section of
  the README for examples.

If an existing workflow depends on the legacy `success` Boolean or its accepted
partial-output convention, keep the legacy node until the downstream graph is
updated for Generate's strict result.

## Release after generation

The optional Generate release policy is not the same as global Release:

- Owned direct mode waits for every direct generation and stops the exact
  process tree before exposing outputs.
- Owned router mode unloads only the exact model used by that generation and
  never stops the router as scoped fallback.
- Attached endpoints are rejected before prompt submission.

Leave the option disabled when subsequent nodes should reuse the same LLM. Use
it when the graph must establish a terminal LLM-to-diffusion GPU handoff.

## Task profiles

Task Profile stores the full selected profile content in the workflow. The
local profile file is only a user-owned editing source. Selection alone changes
nothing; **Update Saved Snapshot** performs the explicit undoable copy.

On a different machine, the saved snapshot executes identically even when its
local profile name is missing. A profile can wrap the user prompt and fill an
exactly empty system prompt. It cannot change model, sampling, seed, images,
constraints, release, or partial policy.

See [Canonical Generate](canonical-generate.md) for the bounded profile document
format and the complete execution contract.

## App Mode on the tested frontend

Frontend 1.45.20 retains terminal text in Comfy's native jobs and history output
but does not render that inline text in App Mode's central result pane. The
canonical workflow therefore exposes transient read-only **Generation Status**
and **Live Response** fields. They are current-session feedback, reset on reload,
and are not serialized or surrogate output files.

## Rollback

Before the post-0.3 release is blessed, switch the installed custom-node clone
back to the accepted 0.3 tag:

```bash
git fetch --tags origin
git switch --detach 0.3.0
```

Restart ComfyUI after switching. Workflows saved with Generate or Task Profile
show those two nodes as missing on 0.3. Existing legacy nodes continue to load.
