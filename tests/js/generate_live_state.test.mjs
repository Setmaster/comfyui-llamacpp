import assert from "node:assert/strict";
import test from "node:test";

import {
    cancellationAction,
    cancellationRetryAction,
    currentWorkflowId,
    isGenerationSnapshot,
    previewText,
    reduceGenerationSnapshot,
    rememberActiveSnapshot,
    requestCancellation,
    resolveExecutionNode,
    snapshotCacheKey,
    snapshotKey,
    snapshotMatchesWorkflow,
    snapshotTargetsNode,
} from "../../web/generate_live_state.js";

const EXEC_A = "00000000-0000-4000-8000-00000000000a";
const EXEC_B = "00000000-0000-4000-8000-00000000000b";
const EXEC_OLD = "00000000-0000-4000-8000-00000000000c";
const EXEC_NEW = "00000000-0000-4000-8000-00000000000d";

function snapshot(overrides = {}) {
    return {
        schema_version: 1,
        kind: "snapshot",
        execution_id: EXEC_A,
        workflow_id: "workflow-a",
        prompt_id: "prompt-a",
        node_id: "dynamic-7",
        display_node_id: "7",
        real_node_id: "7",
        parent_node_id: null,
        list_index: null,
        sequence: 1,
        phase: "starting",
        terminal: false,
        started_at_ms: 1000,
        elapsed_ms: 0,
        response: { text: "", bytes: 0, total_bytes: 0, truncated: false },
        thinking: { text: "", bytes: 0, total_bytes: 0, truncated: false },
        cancel: {
            enabled: true,
            scope: "generation",
            label: "Stop generation",
        },
        ...overrides,
    };
}

test("snapshot validation requires bounded identity fields", () => {
    assert.equal(isGenerationSnapshot(snapshot()), true);
    assert.equal(isGenerationSnapshot({}), false);
    assert.equal(isGenerationSnapshot(snapshot({ execution_id: "" })), false);
    assert.equal(isGenerationSnapshot(snapshot({ sequence: -1 })), false);
    assert.equal(isGenerationSnapshot(snapshot({ started_at_ms: Number.NaN })), false);
    assert.equal(isGenerationSnapshot(snapshot({ execution_id: EXEC_A.toUpperCase() })), false);
    assert.equal(isGenerationSnapshot(snapshot({ execution_id: "not-a-uuid" })), false);
    assert.equal(isGenerationSnapshot(snapshot({ workflow_id: null })), true);
    assert.equal(isGenerationSnapshot(snapshot({ workflow_id: undefined })), false);
    assert.equal(isGenerationSnapshot(snapshot({ workflow_id: "x".repeat(513) })), false);
    assert.equal(isGenerationSnapshot(snapshot({ node_id: "x".repeat(513) })), false);
    assert.equal(isGenerationSnapshot(snapshot({ phase: "complete", terminal: false })), false);
    assert.equal(isGenerationSnapshot(snapshot({ elapsed_ms: Number.MAX_SAFE_INTEGER + 1 })), false);
    assert.equal(
        isGenerationSnapshot(snapshot({ response: { text: "x".repeat(64 * 1024 + 1) } })),
        false,
    );
});

test("same execution accepts only increasing sequence and stable prompt identity", () => {
    const current = snapshot({ sequence: 5, phase: "generating" });
    const newer = snapshot({ sequence: 6, phase: "complete", terminal: true });

    assert.equal(reduceGenerationSnapshot(current, newer), newer);
    assert.equal(reduceGenerationSnapshot(current, snapshot({ sequence: 5 })), current);
    assert.equal(reduceGenerationSnapshot(current, snapshot({ sequence: 4 })), current);
    assert.equal(
        reduceGenerationSnapshot(
            current,
            snapshot({ sequence: 6, prompt_id: "different-prompt" }),
        ),
        current,
    );
    assert.equal(
        reduceGenerationSnapshot(current, snapshot({ sequence: 6, node_id: "spoofed" })),
        current,
    );
    assert.equal(
        reduceGenerationSnapshot(current, snapshot({ sequence: 6, started_at_ms: 1001 })),
        current,
    );

    const terminal = snapshot({ sequence: 7, phase: "complete", terminal: true });
    assert.equal(
        reduceGenerationSnapshot(terminal, snapshot({ sequence: 8, phase: "generating" })),
        terminal,
    );
});

test("late prior execution cannot overwrite a newer run on the same displayed node", () => {
    const current = snapshot({
        execution_id: EXEC_NEW,
        started_at_ms: 2000,
        sequence: 3,
    });
    const old = snapshot({
        execution_id: EXEC_OLD,
        started_at_ms: 1000,
        sequence: 99,
        phase: "complete",
        terminal: true,
    });

    assert.equal(reduceGenerationSnapshot(current, old), current);
});

test("newer mapped execution deterministically becomes the displayed run", () => {
    const first = snapshot({
        execution_id: EXEC_A,
        node_id: "subgraph:7:0",
        list_index: 0,
        started_at_ms: 1000,
    });
    const second = snapshot({
        execution_id: EXEC_B,
        node_id: "subgraph:7:1",
        list_index: 1,
        started_at_ms: 1001,
    });

    assert.equal(reduceGenerationSnapshot(first, second), second);
    assert.deepEqual(snapshotKey(second), [1001, EXEC_B]);
});

test("events for another displayed node are ignored", () => {
    const current = snapshot();
    const other = snapshot({
        execution_id: EXEC_B,
        display_node_id: "8",
        started_at_ms: 2000,
    });

    assert.equal(reduceGenerationSnapshot(current, other), current);
});

test("workflow identity prevents another open workflow from replacing live state", () => {
    const current = snapshot({ workflow_id: "workflow-a" });
    const other = snapshot({
        execution_id: EXEC_B,
        workflow_id: "workflow-b",
        started_at_ms: 2000,
    });

    assert.equal(reduceGenerationSnapshot(current, other), current);
    assert.equal(snapshotMatchesWorkflow(current, "workflow-a"), true);
    assert.equal(snapshotMatchesWorkflow(other, "workflow-a"), false);
    assert.equal(snapshotMatchesWorkflow(snapshot({ workflow_id: null }), null), true);
    assert.equal(currentWorkflowId({ id: "workflow-a" }), "workflow-a");
    assert.equal(currentWorkflowId({ id: "x".repeat(513) }), null);
});

test("compound execution IDs resolve against the root graph", () => {
    const leaf = { id: "63" };
    const innerGraph = {
        getNodeById(id) {
            return id === "63" ? leaf : null;
        },
    };
    const innerContainer = {
        id: "70",
        isSubgraphNode: () => true,
        subgraph: innerGraph,
    };
    const outerGraph = {
        getNodeById(id) {
            return id === "70" ? innerContainer : null;
        },
    };
    const outerContainer = {
        id: "65",
        isSubgraphNode: () => true,
        subgraph: outerGraph,
    };
    const rootGraph = {
        id: "workflow-a",
        getNodeById(id) {
            return id === "65" ? outerContainer : null;
        },
    };
    const incoming = snapshot({ display_node_id: "65:70:63" });

    assert.equal(resolveExecutionNode(rootGraph, "65:70:63"), leaf);
    assert.equal(snapshotTargetsNode(incoming, leaf, rootGraph, "workflow-a"), true);
    assert.equal(snapshotTargetsNode(incoming, leaf, rootGraph, "workflow-b"), false);
    assert.equal(resolveExecutionNode(rootGraph, "65:missing:63"), null);
    assert.equal(resolveExecutionNode(rootGraph, "65::63"), null);
});

test("restoration cache is bounded and terminal snapshots are never retained", () => {
    const cache = new Map();
    for (let index = 0; index < 4; index += 1) {
        assert.equal(
            rememberActiveSnapshot(
                cache,
                snapshot({
                    execution_id: `00000000-0000-4000-8000-00000000000${index}`,
                    display_node_id: String(index),
                    started_at_ms: 1000 + index,
                }),
                2,
            ),
            true,
        );
    }
    assert.deepEqual([...cache.keys()], [
        snapshotCacheKey(snapshot({ display_node_id: "2" })),
        snapshotCacheKey(snapshot({ display_node_id: "3" })),
    ]);
    rememberActiveSnapshot(
        cache,
        snapshot({
            execution_id: "00000000-0000-4000-8000-000000000003",
            display_node_id: "3",
            phase: "complete",
            terminal: true,
            sequence: 2,
            started_at_ms: 1003,
        }),
        2,
    );
    assert.equal(
        cache.has(snapshotCacheKey(snapshot({ display_node_id: "3" }))),
        false,
    );
});

test("restoration keeps the newest mapped execution when active results arrive newest first", () => {
    const cache = new Map();
    const newest = snapshot({
        execution_id: EXEC_NEW,
        started_at_ms: 2000,
        sequence: 3,
    });
    const older = snapshot({
        execution_id: EXEC_OLD,
        started_at_ms: 1000,
        sequence: 20,
    });

    assert.equal(rememberActiveSnapshot(cache, newest), true);
    assert.equal(rememberActiveSnapshot(cache, older), false);
    assert.equal(cache.get(snapshotCacheKey(newest)), newest);

    rememberActiveSnapshot(
        cache,
        { ...older, sequence: 21, phase: "complete", terminal: true },
    );
    assert.equal(cache.get(snapshotCacheKey(newest)), newest);
});

test("preview helpers and cancellation labels never overclaim scope", () => {
    const current = snapshot({
        response: { text: "answer" },
        thinking: { text: "reasoning" },
    });
    assert.equal(previewText(current, "response"), "answer");
    assert.equal(previewText(current, "thinking"), "reasoning");
    assert.deepEqual(cancellationAction(current), {
        enabled: true,
        scope: "generation",
        label: "Stop generation",
    });
    assert.deepEqual(
        cancellationAction(
            snapshot({ cancel: { enabled: true, scope: "prompt", label: "anything" } }),
        ),
        {
            enabled: true,
            scope: "workflow",
            label: "Stop Comfy job",
        },
    );
    assert.equal(
        cancellationAction(
            snapshot({
                prompt_id: null,
                cancel: { enabled: true, scope: "prompt", label: "anything" },
            }),
        ).enabled,
        false,
    );
    assert.equal(cancellationAction(snapshot({ cancel: null })).enabled, false);
    assert.deepEqual(
        cancellationAction(snapshot({ cancel: { enabled: true, scope: "unknown" } })),
        { enabled: false, scope: "none", label: "Stopping unavailable" },
    );
    assert.equal(
        cancellationAction(snapshot({ phase: "complete", terminal: true })).enabled,
        false,
    );
});

test("checked prompt interruption failures remain retryable only for the same execution", async () => {
    const requested = snapshot({
        cancel: { enabled: true, scope: "prompt", label: "Stop Comfy job" },
    });
    const calls = [];
    await assert.rejects(
        requestCancellation(requested, {
            fetchApi: async (...args) => {
                calls.push(args);
                return {
                    ok: false,
                    status: 503,
                    json: async () => ({ error: { message: "interrupt unavailable" } }),
                };
            },
        }),
        /interrupt unavailable/,
    );
    assert.equal(calls[0][0], "/interrupt");
    assert.equal(calls[0][1].method, "POST");
    assert.deepEqual(JSON.parse(calls[0][1].body), { prompt_id: "prompt-a" });

    assert.deepEqual(cancellationRetryAction(snapshot({ sequence: 2 }), requested), {
        enabled: true,
        scope: "generation",
        label: "Stop generation",
    });
    assert.equal(
        cancellationRetryAction(
            snapshot({ execution_id: EXEC_B, sequence: 2 }),
            requested,
        ).enabled,
        false,
    );
    assert.equal(
        cancellationRetryAction(
            snapshot({ sequence: 2, phase: "cancelled", terminal: true }),
            requested,
        ).enabled,
        false,
    );
});

test("exact generation cancellation uses the bounded local route and checks its response", async () => {
    const calls = [];
    const result = await requestCancellation(snapshot(), {
        clientId: "client/a",
        fetchApi: async (...args) => {
            calls.push(args);
            return {
                ok: true,
                status: 200,
                json: async () => ({ upstream_confirmed: true }),
            };
        },
    });

    assert.equal(calls[0][0], "/llamacpp/generation/cancel?client_id=client%2Fa");
    assert.deepEqual(JSON.parse(calls[0][1].body), {
        execution_id: EXEC_A,
        prompt_id: "prompt-a",
        node_id: "dynamic-7",
    });
    assert.deepEqual(result, {
        scope: "generation",
        message: "Exact generation stop confirmed",
    });
});
