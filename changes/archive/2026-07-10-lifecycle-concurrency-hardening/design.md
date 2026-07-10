# Design: lifecycle-concurrency-hardening

Date: 2026-07-10

## Approach

- Keep the existing reentrant operation lock as the single authority. Public operation contexts acquire it, then inspect lifecycle state under the state lock. Generation entry briefly acquires operation then state, increments the lease, and releases operation authority for the generation duration.
- Rework release into an operation-first transaction. Capture the observed release generation read-only, acquire operation authority, coalesce if another release completed while waiting, then mutate pending/in-progress state and execute cleanup while retaining authority.
- On final generation exit, decrement under state and call `request_release` outside the state lock. Do not pre-mark release in progress.
- Wrap manager start/stop/router load/router unload in an idle-required runtime operation before manager/process/client mutation. Cleanup uses an explicit force path.
- Normalize connect and ownership endpoints structurally with `urlsplit` and `ipaddress`; loopback spellings compare equivalent, wildcard binds map to loopback, and IPv6 hosts are bracketed.
- On Linux, launch a small supervisor as the owned process. The supervisor installs `PR_SET_PDEATHSIG` with a dedicated signal, starts llama-server in its unique process group, and kills that group on owner death. Normal controller group signals retain graceful-stop behavior.
- Derive symbolic GPU-layer support from the actual probed `--help` option text. Validate reserved `extra_args` aliases before command construction.

## Data model changes

- Add a runtime-operation busy exception carrying the operation name and active generation count.
- Track the release generation associated with the last result so callers waiting behind another release can coalesce precisely.
- Release result serialization gains an explicit accepted indicator; deferred/coalesced outcomes are accepted but nonterminal.
- Capability snapshots expose whether symbolic GPU-layer values are advertised.

## API / surface changes

- `RuntimeService.serialized_operation` accepts idle requirements and an operation label; manager cleanup uses the non-idle-required path.
- Manager model listing accepts `reload=False` and forwards it to the client.
- Start/stop/load/unload return existing `(success, error)` style busy outcomes rather than mutating then raising.
- Bind hosts remain configuration inputs; `server_url` becomes a normalized connect URL.

## Migration notes

- Existing workflows using unique `extra_args` continue unchanged. Duplicate typed flags must move to their dedicated node/config inputs.
- Existing explicit unload nodes remain available; native Comfy unload now reaches the same backend release path.

## Risks

- Operation/state acquisition order must remain operation then state everywhere except read-only snapshots and generation-exit decrement.
- Linux supervision is platform-specific; unsupported POSIX platforms retain process-group cleanup on normal exit but cannot promise kernel owner-death cleanup.
- Router `failed` is interpreted only as terminal nonresidency during unload, never as a successful load.
