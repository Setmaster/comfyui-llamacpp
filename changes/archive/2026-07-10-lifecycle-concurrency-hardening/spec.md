# Spec: lifecycle-concurrency-hardening

Date: 2026-07-10

## Requirements

- Release state MUST NOT transition until the caller owns shared operation authority.
- A release arriving during startup or stop MUST serialize behind that operation and evaluate the resulting runtime state.
- A final generation lease MUST schedule the deferred release through the same authoritative request path; pending release MUST prevent new generation leases.
- Explicit start, stop, router load, and router unload MUST reject before mutation while generation is active.
- Router load/unload MUST serialize with release and each other. Router unload terminal `failed` MUST be treated as nonresident VRAM state while retaining exact diagnostics.
- Equivalent localhost, IPv4-loopback, and IPv6-loopback URLs MUST retain managed ownership for the same scheme, port, and API path.
- Wildcard bind hosts MUST produce a loopback client URL; IPv6 URLs MUST be bracketed; Unix-socket host forms MUST be rejected.
- On Linux, abrupt owner death MUST terminate the uniquely owned server process group without broad process matching. Windows Job Object behavior MUST remain intact.
- `version: 9957 (...)` MUST yield build 9957. Symbolic GPU layers (`auto`, `all`) MUST be accepted only when probed help advertises them.
- `extra_args` MUST reject aliases that duplicate typed transport, identity/model, auth, TLS, router, or lifecycle options.
- Deferred/coalesced release requests MUST be observable as accepted, nonterminal outcomes rather than failures.

## Scenarios

- `/free` begins while startup owns the operation lock: it waits, then releases the newly managed runtime.
- `/free` begins while stop owns the operation lock: it waits, then reports the already-cleared runtime without corrupting state.
- Router load wins the operation lock before `/free`: release subsequently unloads the loaded model. `/free` wins first: load executes afterward as a new operation.
- Generation is active and explicit stop/load/unload is requested: the request returns a busy error and leaves runtime/process/model state unchanged.
- Release is requested during generation: it returns deferred; the final lease exits; exactly one authoritative release runs.
- Managed server bound to `127.0.0.1` is used through `localhost`, or bound to `::1` and used through `[::1]`: generation retains the managed lease.
- Linux controller owner exits with `os._exit`: supervisor kills its own process group and the server descendant disappears.
- Router reports model state `failed` after unload: router remains alive, release reports success/nonresidency and preserves that state in diagnostics.

## Open questions

- None. The chosen explicit-operation contract is reject-before-mutation while generation is active; native release remains deferred.
