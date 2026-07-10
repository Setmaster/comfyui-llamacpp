# Spec: sota-refactor

Date: 2026-07-10

## Requirements

### Compatibility

- Preserve released node class IDs, display mappings, `FUNCTION` names, return types, return names, output order, socket names, defaults, and legacy primitive widget prefixes.
- Append new primitive widgets only after each node's final legacy primitive widget.
- Preserve root imports from `server_manager`, `model_manager`, and `streaming_client` through compatibility facades.
- Preserve `models/LLM/gguf` as the default model location while honoring configured Comfy model paths.

### Lifecycle and ownership

- Only processes started and positively identified by this pack may be terminated.
- POSIX launches use an owned process group. Windows launches use a fresh Job Object per launch when available, with a validated descendant fallback.
- Server stdout and stderr are continuously drained into a bounded redacted log tail.
- Start, stop, reconfigure, router operations, native release, and generation leases are coordinated without races.
- Public stopped status is reported only after the owned process tree is gone.
- Unexpected exits retain diagnostics and enter a failure state.
- Explicit stop nodes continue to work.

### llama-server protocol

- Use only `/models/load` and `/models/unload` for router residency changes.
- Treat HTTP success as acceptance and poll `/models` until the requested terminal state.
- Normalize current router states including `unloaded`, `loading`, `loaded`, `sleeping`, download states, and failed metadata.
- Use exact IDs or aliases returned by the router and fail on ambiguity.
- Centralize endpoint normalization, timeouts, authentication, TLS verification, error parsing, and secret redaction.
- Probe binary help/version and expose or pass modern optional CLI flags only when supported.
- Preserve legacy server defaults unless users explicitly choose new settings.

### Comfy integration

- Observe successful `POST /free` and `POST /api/free` requests without replacing frontend commands or core model-management functions.
- A native unload request releases an idle owned direct server immediately, or unloads resident router models to terminal state.
- Release requested during generation is deferred until the final active lease exits.
- Concurrent release requests coalesce.
- Attached or remote endpoints are never changed by implicit Comfy release.
- Middleware installation is idempotent and fail-open. Explicit nodes remain available if the bridge cannot install.
- Optional pre-start handoff asks Comfy to evict managed models before an owned llama-server allocates GPU memory.

### Generation and nodes

- Basic, ADV, and ADV++ prompts share one request path while retaining their distinct public schemas.
- Python declares `image_1` through `image_10`; image count 0 is valid; conversion failures are visible.
- ADV++ templates are applied in Python so API and frontend workflows behave the same.
- Streaming has an overall monotonic deadline, closes responses on every path, preserves partial diagnostics, and requires a valid terminal condition for success.
- Structured output supports validated JSON object, nested JSON schema, direct schema, and grammar constraints where the server supports them.
- Token Count and Model Info use the shared authenticated client and exact router model routing.
- Stop sequences and token bans accept robust list forms while retaining legacy comma and multiline behavior.
- Status surfaces ownership, mode, lifecycle stage, process identity, model residency, pending release, capability information, and bounded logs without secrets.

### Quality and release

- Add automated Python and JavaScript tests, workflow fixtures, process helpers, fake HTTP integration, lint configuration, CI, and package build checks.
- Add a real LICENSE file matching project metadata and make version metadata single-source.
- Document installation, upgrade compatibility, model layout, direct and router workflows, native unload behavior, security boundaries, troubleshooting, and user acceptance steps.
- Keep all cloud integrations out of the required dependency and test surface.

## Scenarios

1. An old saved Basic Prompt workflow loads and all legacy widget values retain their original meaning.
2. ADV and ADV++ workflows with 0, 1, or 10 connected images execute through Python with the same socket names after reload.
3. Starting with the same effective configuration is idempotent; changing any effective field performs a coordinated restart.
4. An unrelated process named `llama-server` remains alive when this pack stops its owned process tree.
5. A noisy child cannot fill a pipe and deadlock ComfyUI.
6. A direct owned server exits completely when either native Comfy unload command is invoked.
7. A router model unload node returns only after the model reaches terminal `unloaded` state.
8. A native unload request during streaming waits for the request lease to finish, then releases resources.
9. An attached remote endpoint is usable for generation but is never implicitly stopped or unloaded.
10. Authentication applies to every protected endpoint, while commands, logs, status, and workflows do not expose the secret.
11. A traversal or symlink escape in a model selection is rejected.
12. Comfy can evict its managed diffusion models before an owned LLM starts, then reclaim the GPU after native LLM release.
13. The package imports in a current ComfyUI checkout, builds as a Python package, and passes Registry-oriented validation without being published.

## Open questions

- None blocking. New behaviors that could surprise existing workflows use compatibility-preserving defaults and are documented as opt-in where appropriate.
