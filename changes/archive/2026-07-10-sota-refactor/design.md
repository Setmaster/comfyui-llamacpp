# Design: sota-refactor

Date: 2026-07-10

## Approach

- Keep the existing V1 nodes as thin public facades and extract reusable internals incrementally behind characterization tests.
- Create four internal layers:
  - `runtime/`: configuration, capability probes, owned process control, lifecycle service, llama-server HTTP client, streaming, and Comfy bridge.
  - `generation/`: immutable options, payload construction, image conversion, structured constraints, and templates.
  - `models/`: Comfy-aware catalog and exact router identity resolution.
  - `nodes/`: ordered compatibility schemas and lightweight Comfy adapters.
- Keep root `server_manager.py`, `model_manager.py`, and `streaming_client.py` as documented facades.
- Use a single `RuntimeService` instance for ownership, lifecycle state, active request leases, release coalescing, and diagnostics.
- Make router polling authoritative. SSE can accelerate observability but cannot be the sole completion barrier.
- Add a narrowly scoped aiohttp middleware during custom-node import to observe successful Comfy free requests after the core handler.
- Modernize frontend code through official extension lifecycle hooks, without patching core commands or global prototypes.

## Data model changes

- Immutable direct-server and router configuration objects include every effective CLI field, normalized binary identity, and a complete stable fingerprint.
- A process record stores PID, creation identity, platform ownership handle or process group, command with secret fields redacted, mode, start time, exit state, and bounded log tail.
- Lifecycle state distinguishes stopped, preflight, Comfy eviction, spawning, readiness, direct ready, router ready, stopping, start failure, runtime failure, and incomplete stop.
- A connection profile stores normalized base URL, optional model, authentication source, TLS policy, ownership kind, and request deadlines. Secrets are read from environment variables and excluded from serialization and status.
- Router model records preserve exact IDs, aliases, nested status values, failure metadata, and residency state.
- Generation results preserve text, reasoning, success, finish reason, usage, partial data, and structured error information while legacy nodes continue returning their existing tuples.

## API / surface changes

- Existing nodes and outputs remain stable.
- New widgets are appended and optional. Likely additions include API-key environment variable, TLS verification, request deadline, idle sleep, model limits, reverse Comfy eviction, and advanced modern llama-server controls where capability probes succeed.
- Add a reusable connection/profile node only if it reduces repeated configuration without forcing migration from `server_url` widgets.
- Add namespaced backend status and release endpoints for observability and explicit fallback.
- Add a namespaced lifecycle event for release failures and important degraded ownership states.
- Status output expands in human-readable and machine-readable forms without removing existing status strings.
- Explicit Stop Server and router Unload Model remain first-class controls.

## Migration notes

- Legacy primitive widget order is frozen and tested. Dirty-bundle widgets are relocated only where needed to restore that prefix before they become released behavior.
- Ten optional image sockets become Python-declared inputs. JavaScript hides or shows sockets and restores graph presentation, but backend execution no longer depends on undeclared dynamic keys.
- `keep_context` retains its public name and boolean default; documentation changes from conversation memory to prompt-prefix cache reuse.
- Existing hardcoded model directory remains the default fallback. `folder_paths` registration adds standard discovery rather than relocating users' files.
- Root modules retain prior names and functions as forwarding wrappers during the entire refactor.
- V3 nodes are parked until their API stability and a safe migration path justify a separate release.

## Risks

- Windows process creation and Job Object assignment require native code paths that cannot be fully exercised from Linux. A real Windows Comfy smoke test is mandatory.
- The Comfy free middleware relies on current startup ordering because there is no official external-resource callback. Installation failure must never prevent node registration.
- Legacy user workflows may contain undocumented widget states. Fixture coverage will include multiple historical commits and representative serialized graphs.
- llama-server CLI and JSON shapes can change independently. Capability probes and tolerant readers should accept known compatible shapes while writes remain strict.
- A graceful direct stop can interrupt a request if lease tracking is bypassed. All generation-capable nodes must use the shared lease.
- Broad extraction can create circular imports in a Comfy plugin environment. Internal layers must remain importable without a running Comfy installation wherever possible.

## Key design decisions

- Use V1 compatibility surfaces now, not a dual V1/V3 export that doubles runtime paths.
- Never register a fake Comfy `ModelPatcher` for llama-server.
- Never monkeypatch `comfy.model_management`, frontend memory commands, or global fetch.
- Never sweep process names or adopt an existing service based only on a healthy port.
- Never use POST or DELETE `/models` as load/unload fallbacks.
- Default implicit release applies only to owned local runtimes.
