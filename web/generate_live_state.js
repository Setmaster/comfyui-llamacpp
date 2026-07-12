export const LIVE_SCHEMA_VERSION = 1;
export const LIVE_EVENT_NAME = "llamacpp.generation";
const MAX_IDENTIFIER_BYTES = 512;
const MAX_PREVIEW_BYTES = 64 * 1024;
const UTF8_ENCODER = new TextEncoder();
const UUID4_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const LIVE_PHASES = new Set([
    "starting",
    "loading",
    "reasoning",
    "generating",
    "cancelling",
    "releasing",
    "complete",
    "cancelled",
    "failed",
]);
const TERMINAL_PHASES = new Set(["complete", "cancelled", "failed"]);

function finiteNumber(value) {
    return typeof value === "number" && Number.isFinite(value);
}

function text(value) {
    return typeof value === "string" ? value : "";
}

function boundedText(value, { optional = false } = {}) {
    if (optional && value === null) return true;
    if (typeof value !== "string" || value.length === 0) return false;
    if (value.length > MAX_IDENTIFIER_BYTES) return false;
    return UTF8_ENCODER.encode(value).byteLength <= MAX_IDENTIFIER_BYTES;
}

function boundedPreview(value) {
    if (!value || typeof value.text !== "string") return false;
    if (value.text.length > MAX_PREVIEW_BYTES) return false;
    return UTF8_ENCODER.encode(value.text).byteLength <= MAX_PREVIEW_BYTES;
}

export function snapshotKey(snapshot) {
    return [
        finiteNumber(snapshot?.started_at_ms) ? snapshot.started_at_ms : 0,
        text(snapshot?.execution_id),
    ];
}

export function currentWorkflowId(rootGraph) {
    const value = rootGraph?.id;
    return boundedText(value, { optional: true }) ? value : null;
}

export function snapshotMatchesWorkflow(snapshot, workflowId) {
    const current = boundedText(workflowId, { optional: true }) ? workflowId : null;
    return isGenerationSnapshot(snapshot) && snapshot.workflow_id === current;
}

export function snapshotCacheKey(snapshot) {
    if (!isGenerationSnapshot(snapshot)) return null;
    return JSON.stringify([snapshot.workflow_id, snapshot.display_node_id]);
}

/** Resolve Comfy's root-relative execution path, including nested subgraphs. */
export function resolveExecutionNode(rootGraph, executionId) {
    if (!rootGraph || typeof rootGraph.getNodeById !== "function") return null;
    if (typeof executionId !== "string" || !executionId) return null;
    const path = executionId.split(":");
    if (path.some((part) => !part)) return null;
    let graph = rootGraph;
    for (let index = 0; index < path.length; index += 1) {
        const node = graph.getNodeById(path[index]);
        if (!node) return null;
        if (index === path.length - 1) return node;
        if (!node.isSubgraphNode?.() || !node.subgraph) return null;
        graph = node.subgraph;
    }
    return null;
}

export function snapshotTargetsNode(snapshot, node, rootGraph, workflowId) {
    return (
        snapshotMatchesWorkflow(snapshot, workflowId) &&
        resolveExecutionNode(rootGraph, snapshot.display_node_id) === node
    );
}

export function isGenerationSnapshot(value) {
    return Boolean(
        value &&
            value.schema_version === LIVE_SCHEMA_VERSION &&
            value.kind === "snapshot" &&
            UUID4_PATTERN.test(text(value.execution_id)) &&
            boundedText(value.workflow_id, { optional: true }) &&
            boundedText(value.prompt_id, { optional: true }) &&
            boundedText(value.node_id) &&
            boundedText(value.display_node_id) &&
            boundedText(value.real_node_id) &&
            boundedText(value.parent_node_id, { optional: true }) &&
            (value.list_index === null ||
                (Number.isSafeInteger(value.list_index) && value.list_index >= 0)) &&
            Number.isSafeInteger(value.sequence) &&
            value.sequence >= 1 &&
            Number.isSafeInteger(value.started_at_ms) &&
            value.started_at_ms >= 0 &&
            Number.isSafeInteger(value.elapsed_ms) &&
            value.elapsed_ms >= 0 &&
            LIVE_PHASES.has(value.phase) &&
            typeof value.terminal === "boolean" &&
            value.terminal === TERMINAL_PHASES.has(value.phase) &&
            boundedPreview(value.response) &&
            boundedPreview(value.thinking),
    );
}

function compareStart(left, right) {
    const [leftTime, leftId] = snapshotKey(left);
    const [rightTime, rightId] = snapshotKey(right);
    if (leftTime !== rightTime) return leftTime - rightTime;
    return leftId.localeCompare(rightId);
}

/**
 * Reduce one displayed node's live state.
 *
 * An execution UUID plus prompt ID owns an active state. A newer execution may
 * replace it, while late or duplicate sequences from an older execution are
 * rejected. The backend result remains independent when list mapping causes
 * several dynamic executions to share one displayed node.
 */
export function reduceGenerationSnapshot(current, incoming) {
    if (!isGenerationSnapshot(incoming)) return current ?? null;
    if (!isGenerationSnapshot(current)) return incoming;
    if (
        incoming.workflow_id !== current.workflow_id ||
        incoming.display_node_id !== current.display_node_id
    ) {
        return current;
    }

    const sameExecution = incoming.execution_id === current.execution_id;
    if (sameExecution) {
        const identityChanged =
            incoming.prompt_id !== current.prompt_id ||
            incoming.node_id !== current.node_id ||
            incoming.real_node_id !== current.real_node_id ||
            incoming.parent_node_id !== current.parent_node_id ||
            incoming.list_index !== current.list_index ||
            incoming.started_at_ms !== current.started_at_ms;
        if (
            identityChanged ||
            incoming.sequence <= current.sequence ||
            (current.terminal && !incoming.terminal)
        ) {
            return current;
        }
        return incoming;
    }

    return compareStart(incoming, current) > 0 ? incoming : current;
}

export function rememberActiveSnapshot(cache, snapshot, maximum = 32) {
    if (!(cache instanceof Map) || !isGenerationSnapshot(snapshot)) return false;
    const key = snapshotCacheKey(snapshot);
    const current = cache.get(key);
    const next = reduceGenerationSnapshot(current, snapshot);
    if (snapshot.terminal) {
        if (!current || next === snapshot) cache.delete(key);
        return true;
    }
    if (next !== snapshot) return false;
    cache.delete(key);
    cache.set(key, snapshot);
    const limit = Number.isSafeInteger(maximum) && maximum > 0 ? maximum : 32;
    while (cache.size > limit) {
        cache.delete(cache.keys().next().value);
    }
    return true;
}

export function previewText(snapshot, pane) {
    const value = snapshot?.[pane];
    return value && typeof value.text === "string" ? value.text : "";
}

export function cancellationAction(snapshot) {
    if (!isGenerationSnapshot(snapshot) || snapshot.terminal) {
        return { enabled: false, scope: "none", label: "Stopping unavailable" };
    }
    const cancel = snapshot?.cancel;
    if (!cancel || cancel.enabled !== true) {
        return { enabled: false, scope: "none", label: "Stopping unavailable" };
    }
    if (cancel.scope !== "generation" && cancel.scope !== "prompt") {
        return { enabled: false, scope: "none", label: "Stopping unavailable" };
    }
    if (cancel.scope === "prompt" && !snapshot.prompt_id) {
        return { enabled: false, scope: "none", label: "Stopping unavailable" };
    }
    const scope = cancel.scope === "generation" ? "generation" : "workflow";
    return {
        enabled: true,
        scope,
        label: scope === "generation" ? "Stop generation" : "Stop Comfy job",
    };
}

export function cancellationRetryAction(current, requested) {
    if (!isGenerationSnapshot(current) || !isGenerationSnapshot(requested)) {
        return { enabled: false, scope: "none", label: "Stopping unavailable" };
    }
    const sameExecution =
        current.execution_id === requested.execution_id &&
        current.workflow_id === requested.workflow_id &&
        current.prompt_id === requested.prompt_id &&
        current.node_id === requested.node_id;
    return sameExecution
        ? cancellationAction(current)
        : { enabled: false, scope: "none", label: "Stopping unavailable" };
}

async function responsePayload(response) {
    try {
        return await response?.json?.();
    } catch {
        return null;
    }
}

function responseError(payload, status) {
    const message = text(payload?.error?.message) || text(payload?.status);
    return message.slice(0, 512) || `HTTP ${status ?? "error"}`;
}

/** Issue one checked, identity-scoped cancellation request. */
export async function requestCancellation(
    snapshot,
    {
        fetchApi,
        clientId = null,
        cancelRoute = "/llamacpp/generation/cancel",
        interruptRoute = "/interrupt",
    } = {},
) {
    const action = cancellationAction(snapshot);
    if (!action.enabled) throw new Error("Stopping is unavailable for this generation");
    if (typeof fetchApi !== "function") throw new Error("Comfy request transport is unavailable");

    if (action.scope === "workflow") {
        const response = await fetchApi(interruptRoute, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ prompt_id: snapshot.prompt_id }),
        });
        if (!response?.ok) {
            throw new Error(responseError(await responsePayload(response), response?.status));
        }
        return {
            scope: "workflow",
            message: "Prompt-targeted Comfy interruption requested",
        };
    }

    if (!boundedText(clientId)) throw new Error("Comfy client identity is unavailable");
    const response = await fetchApi(
        `${cancelRoute}?client_id=${encodeURIComponent(clientId)}`,
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                execution_id: snapshot.execution_id,
                prompt_id: snapshot.prompt_id,
                node_id: snapshot.node_id,
            }),
        },
    );
    const payload = await responsePayload(response);
    if (!response?.ok) {
        throw new Error(responseError(payload, response?.status));
    }
    return {
        scope: "generation",
        message: payload?.upstream_confirmed
            ? "Exact generation stop confirmed"
            : "Exact generation stop requested; upstream confirmation unavailable",
    };
}
