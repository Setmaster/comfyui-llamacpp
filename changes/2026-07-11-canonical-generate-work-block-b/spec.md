# Spec: canonical-generate-work-block-b

Date: 2026-07-11

## Requirements

1. Preserve the complete public contracts and behavior of all 17 released nodes.
2. Add only `LlamaCppGenerate` and `LlamaCppTaskProfile` as public nodes.
3. Generate must accept either an existing typed Connection or an advanced URL,
   use the current managed runtime when both are absent, and reject both together.
4. Router generation must lease and send one exact canonical model ID.
5. Thinking Auto omits the override. Off and On send explicit Boolean values.
6. Sampling Default omits all custom sampler keys. Custom sends the complete
   expert sampler group.
7. A connected profile uses only its saved snapshot. It may fill an exactly empty
   system prompt and apply its prefix/suffix, but cannot override controls.
8. Freeform is the only built-in profile. User profiles are bounded, per-user,
   update-safe, refreshable without restart, and explicitly copied into snapshots.
9. Total failures and default-policy partial failures raise. Partial output reaches
   sockets only under the explicit Return Marked Partial policy.
10. JSON object/schema modes receive post-generation syntactic JSON validation.
11. Live preview is execution-scoped, bounded, nonserialized, targeted, reload
    restorable, and stale-event resistant in classic and Nodes 2.0.
12. Supported llama.cpp exact Stop must delete its upstream replay session on
    every exit path. Unsupported endpoints must expose only truthful whole-job
    cancellation.
13. Passive discovery must always use `autoload=false`, report Known/Unknown,
    preserve missing saved values, and never auto-confirm a projector.
14. Runtime epochs must reject mixed-runtime discovery and stale scoped mutation.
15. Direct scoped release waits for every direct lease. Router scoped release
    waits only for the exact model and cannot stop the router as fallback.
16. Accepted cleanup continues after caller timeout or interruption. Outputs are
    withheld until the caller observes terminal cleanup or raises.
17. Attached endpoints are never mutated and canonical release-after is rejected
    before prompt submission.
18. Native global release and every existing explicit lifecycle node retain their
    current contracts.
19. Events, results, errors, logs, routes, and profiles never expose secrets or
    arbitrary raw server bodies.
20. No deferred product family may enter this work block.

## Scenarios

### Simple managed text

Start Server's existing URL links to Generate. Generate sends Default sampling,
Auto thinking, and a random seed; streams bounded status; then returns response,
thinking, and a complete rich result.

### Typed attached connection

Connection links to Generate with an environment-variable key name. The secret is
resolved only on the backend. Generation works, but selecting release-after fails
before any request because ownership is external.

### Router exact model

Start Router links to Generate. Model refresh shows only authoritative router
identities and residency. A missing saved model remains visible and raw. Generate
resolves one exact model, leases it, sends that ID, and rejects any conflicting
response model.

### Passive cold inspection

An unloaded router model is selected. Discovery calls passive props with
`autoload=false` and the live list without reload. Unsupported facts are shown
only when explicit; missing facts are Unknown. No child load, model event, or VRAM
allocation occurs.

### Exact local Stop

The support probe succeeds. Generate uses an internal conversation UUID, prompt
progress, and one-second SSE pings. `Stop generation` cancels only this execution,
deletes the upstream stream, closes the response, exits the lease, leaves bounded
partial preview visible, and follows the selected partial-output policy.

### Honest fallback Stop

The support probe is unsupported or Unknown. The UI says `Stop Comfy job` and
uses prompt-targeted Comfy interruption. It does not claim node-local latency.

### Direct terminal release

Two direct generations overlap. One requests release. Its handle arms before its
lease exits, blocks new direct admission, waits for the other existing lease, and
completes only when the owned process tree has stopped.

### Router scoped release

Models A and B are resident and B is active. A requests release. New A admission
is blocked, B remains admissible, A waits only for A leases, and one terminal
unload of exact A occurs. Any failure leaves router/B intact.

### User profile portability

The user refreshes profiles, chooses a local entry, and explicitly updates the
node snapshot through an undoable action. Another machine without that named
entry executes the saved snapshot identically and reports local profile status as
missing without altering the snapshot.

### Strict failure

Authentication, TLS, model, timeout, transport, protocol, server, structured JSON,
or release failure raises one categorized, redacted exception. No downstream node
runs and no `Error: ...` response string is emitted.

## Open questions

- None at architecture freeze. Implementation may refine private type names or
  file boundaries without changing these observable contracts.
