# Proposal: lifecycle-concurrency-hardening

Date: 2026-07-10

> Do not put secrets in this folder. Use 1Password.

## Why

- Native Comfy release requests can currently observe or mutate lifecycle state before they own the shared runtime operation lock. A `/free` request that arrives during startup can therefore return a no-op while the server finishes starting, and router load/unload can race release.
- Explicit start/stop/reconfigure operations can currently begin destructive work during generation and only fail after the runtime has already changed.
- Managed-endpoint ownership is based on URL-string equality, so equivalent loopback spellings and IPv6/wildcard bind addresses can lose their lease and skip cleanup.
- Linux child processes survive an abrupt Comfy owner exit; Windows Job fallback state is reported before any launch attempt.
- Real llama.cpp version/help output and symbolic GPU-layer support need capability-based validation, and raw extra arguments must not override typed lifecycle/security settings.

## Scope

- Make one operation lock the authority for startup, stop, release, router load, and router unload; preserve generation release deferral and request coalescing.
- Give explicit runtime operations a deterministic busy contract while generation is active.
- Normalize managed loopback/IPv6 endpoints, return safe connect URLs for wildcard binds, and reject Unix-socket host syntax.
- Add Linux owner-death supervision without process-name matching; retain Windows Job Objects and accurately expose fallback state.
- Parse contemporary llama.cpp build output, gate symbolic GPU-layer values from probed help, and reject reserved typed flags in `extra_args`.
- Add concurrency, endpoint, capability, and real subprocess regression tests.

## Non-goals

- Change the public node graph or remove the existing explicit unload nodes.
- Add Unix-domain-socket transport.
- Kill an attached external server or a router process when unloading router models.
- Implement cross-process distributed locking.

## Risks

- Lock-order mistakes can deadlock generation and release; all paths must acquire operation authority before lifecycle-state mutation.
- A Linux supervisor changes the directly owned PID from llama-server to a small Python supervisor, although signals and logs still cover the full process group.
- Stricter `extra_args` validation can reject workflows that previously relied on ambiguous duplicate flags.

## Rollback

- Revert the runtime/service, manager, process, capability, config, and focused test changes as one bundle. No persisted data migration is involved.
