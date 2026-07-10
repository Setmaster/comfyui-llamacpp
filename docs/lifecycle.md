# Lifecycle and VRAM ownership

This document describes what the pack owns, what ComfyUI owns, and what an
unload result actually proves.

## Ownership boundary

There are three runtime modes:

| Mode | Process owner | Implicit native release authority |
| --- | --- | --- |
| Direct | This pack started one model server | Stop the exact owned process tree |
| Router | This pack started one router | Unload resident child models, or stop the exact router tree as fallback |
| Attached | Another user or service started the endpoint | None |

An explicit URL does not grant process ownership. Attached endpoints are usable
for generation, properties, and tokenization, but native Comfy unload never
changes their residency.

## Why Comfy needs a bridge

ComfyUI can unload models registered through its model-management system. An
external service is outside that registry. Version 0.3 installs a narrow,
fail-open middleware before the aiohttp application freezes. After Comfy
successfully handles `POST /free` or `POST /api/free`, the middleware checks for
`unload_models` or `free_memory` and asks this pack's coordinator to release its
owned resources.

The core Comfy handler always runs first. A plugin failure does not turn a
successful Comfy unload into an HTTP failure. The response includes an
`X-ComfyUI-LlamaCpp-Release` header when the bridge completed its decision, and
the backend emits a `llamacpp.lifecycle` event.

Diagnostic API routes are also available:

- `GET /llamacpp/runtime/status`
- `POST /llamacpp/runtime/release`

HTTP-visible diagnostics withhold raw backend exception material. The Server
Status node and local Comfy log retain useful redacted details.

These routes use ComfyUI's network trust boundary and do not add separate
authentication. Status can disclose local executable, model, and working
directory paths plus a redacted log tail. Release can stop or unload this
pack's owned runtime. Bind ComfyUI to loopback or place it behind trusted
authentication when those capabilities should not be exposed to network
clients.

Status remains available while startup, shutdown, release, or a router barrier
is in progress. Service and process fields are captured independently under
short locks, so a transitional response can describe adjacent moments rather
than one atomic lifecycle instant.

## Completion barriers

An unload request is not complete merely because an HTTP endpoint accepted it.

Direct completion requires:

1. Signal the positively owned process tree.
2. Wait for it to exit.
3. Escalate when the graceful deadline expires.
4. Wait again and confirm no owned member remains.

Router completion requires:

1. Read exact model IDs and states from `/models`.
2. Request unload through `/models/unload`.
3. Poll `/models` until each target is nonresident.
4. If state is ambiguous or the barrier fails, stop the owned router tree as a
   conservative fallback.

Driver-visible VRAM can converge slightly after the process or child exits.
For release validation, observe `nvidia-smi` or the equivalent backend tool and
also prove that the next GPU workload can allocate and run.

## Concurrency

Start, replacement, stop, router mutation, release, and generation leases share
one lifecycle authority.

- Native release during managed generation is deferred.
- New managed generation is rejected while release is pending or running.
- Router mutations and replacements do not run through an active generation.
- Router catalog reload is treated as a mutation and uses the same idle
  lifecycle barrier.
- Concurrent release requests share one result or observe the resulting idle
  state.
- A failed replacement preflight leaves a healthy existing runtime intact.

The explicit Stop node is intentionally separate from native release. Its exact
behavior during an already-running generation is reported rather than leaving
half-cleared state. Use Comfy's interrupt control before an emergency stop when
possible.

## Process ownership

### Windows

Each launch attempts to create a fresh Job Object, enable kill-on-job-close,
and assign the spawned server. Closing the Job handle during ordinary stop or
abrupt Comfy termination kills the contained tree.

If Job assignment is unavailable, the controller retains validated descendants
using PID plus creation-time identity. This fallback is enough for deterministic
ordinary stop but cannot provide the same abrupt-owner guarantee. Server Status
reports `windows_job_assigned` and `descendant_fallback`; treat fallback as a
degraded result in release testing.

### Linux and other POSIX systems

Each launch receives a new session and process group distinct from ComfyUI.
Signals target only that verified group. On Linux, a small owned supervisor uses
the kernel's parent-death signal facility to tie that group to the owning Comfy
process, so a hard owner exit does not leave llama.cpp workers behind. The PID
reported by Server Status is therefore the supervisor/group leader on Linux;
`llama-server` is its child.

If a POSIX leader exits unexpectedly, the controller observes it without
reaping it. That held leader keeps the original process-group generation from
being reused while surviving members are cleaned up. The group authority is
retired before the leader is finally reaped. If another component has already
reaped the leader, cleanup fails closed and never signals the stale numeric
group ID.

Other POSIX systems retain exact process-group cleanup for explicit stop and
normal Comfy exit, but they do not currently provide the same abrupt-owner
guarantee.

The implementation never discovers targets by executable name.

## Reverse handoff before LLM start

`unload_comfy_models_before_start` calls Comfy's model-management eviction and
cache release before starting llama.cpp. This can free diffusion, text encoder,
or VAE allocations that Comfy knows about. It cannot unload arbitrary global
objects owned by unrelated custom nodes.

A strict shared-GPU test is therefore:

1. Run a diffusion workload and record memory.
2. Start the LLM with Comfy pre-eviction enabled.
3. Generate through the LLM.
4. Invoke native or explicit LLM release and wait for completion.
5. Confirm driver-visible memory returns near the expected baseline.
6. Run the diffusion workload again without restarting ComfyUI.

Functional reallocation is the decisive final assertion.
