import { api } from "../../scripts/api.js";
import { app } from "../../scripts/app.js";
import { ComfyWidgets } from "../../scripts/widgets.js";
import {
    EXPERT_SAMPLERS,
    RUNNING_MODEL,
    applySavedModelHint,
    formatGenerationStatus,
    installPostConfigureReconciliation,
    markWidgetReadOnly,
    normalizeDiscovery,
    reconcileDiscoverySelection,
    shouldShowExpertSampling,
} from "./generate_controls.js";
import {
    LIVE_EVENT_NAME,
    cancellationAction,
    cancellationRetryAction,
    currentWorkflowId,
    previewText,
    reduceGenerationSnapshot,
    rememberActiveSnapshot,
    requestCancellation,
    resolveExecutionNode,
    snapshotMatchesWorkflow,
    snapshotTargetsNode,
} from "./generate_live_state.js";

const TARGET = "LlamaCppGenerate";
const DISCOVERY_ROUTE = "/llamacpp/runtime/discovery";
const ACTIVE_ROUTE = "/llamacpp/generation/active";
const CANCEL_ROUTE = "/llamacpp/generation/cancel";
const nodes = new Set();
const restored = new Map();
const discoveryRequests = new Map();
let automaticDiscoveryCache = null;
let automaticDiscoveryCachedAt = 0;
let restoredClientId = null;
let restoredWorkflowId = null;
let restorePromise = null;
let restoreAgain = false;

function transient(widget) {
    widget.options ??= {};
    widget.options.serialize = false;
    widget.serialize = false;
    widget.serializeValue = () => undefined;
    return widget;
}

function readOnlyText(node, name, value, { multiline = false } = {}) {
    const widget = markWidgetReadOnly(transient(
        ComfyWidgets.STRING(
            node,
            name,
            ["STRING", { multiline, default: value, read_only: true }],
            app,
        ).widget,
    ));
    widget.value = value;
    return widget;
}

function getRootGraph() {
    try {
        return app.rootGraph ?? app.graph ?? null;
    } catch {
        return app.graph ?? null;
    }
}

function getWorkflowId() {
    return currentWorkflowId(getRootGraph());
}

function fit(node) {
    const current = node.size ?? [360, 100];
    const computed = node.computeSize?.() ?? current;
    node.setSize?.([
        Math.max(420, current[0], computed[0]),
        Math.max(current[1], computed[1]),
    ]);
    node.graph?.setDirtyCanvas?.(true, true);
}

function widget(node, name) {
    return node.widgets?.find((candidate) => candidate.name === name);
}

function inputLinked(node, name) {
    return (node.inputs ?? []).some(
        (input) =>
            (input.name === name || input.widget?.name === name) && input.link != null,
    );
}

function setWidgetHidden(widgetValue, hidden) {
    if (!widgetValue) return;
    if (!("__llamacppOriginalComputeSize" in widgetValue)) {
        widgetValue.__llamacppOriginalComputeSize = widgetValue.computeSize;
    }
    widgetValue.options ??= {};
    widgetValue.options.hidden = hidden;
    widgetValue.hidden = hidden;
    widgetValue.computeSize = hidden
        ? () => [0, -4]
        : widgetValue.__llamacppOriginalComputeSize;
}

function syncSampling(node) {
    const mode = widget(node, "sampling_mode");
    const custom = shouldShowExpertSampling(
        mode?.value,
        inputLinked(node, "sampling_mode"),
    );
    for (const name of EXPERT_SAMPLERS) {
        setWidgetHidden(widget(node, name), !custom && !inputLinked(node, name));
    }
    fit(node);
}

function installConfiguredReconciliation(node) {
    installPostConfigureReconciliation(
        node,
        "__llamacppGeneratePostConfigure",
        (configured) => {
            installSampling(configured);
            syncSampling(configured);
        },
    );
}

function installSampling(node) {
    const mode = widget(node, "sampling_mode");
    if (!mode || mode.__llamacppSamplingMode) return;
    mode.__llamacppSamplingMode = true;
    const original = mode.callback;
    mode.callback = function (value, ...args) {
        const result = original?.call(this, value, ...args);
        syncSampling(node);
        return result;
    };
    const originalConnections = node.onConnectionsChange;
    node.onConnectionsChange = function (...args) {
        const result = originalConnections?.apply(this, args);
        syncSampling(this);
        return result;
    };
    syncSampling(node);
}

async function refreshDiscovery(node, { automatic = false } = {}) {
    const state = node.__llamacppGenerateState;
    if (!state) return;
    if (state.discoveryPending) {
        if (!automatic) state.discoveryManualAfter = true;
        return;
    }
    state.discoveryPending = true;
    state.modelStatus.value = "Refreshing managed runtime facts...";
    const requested = String(state.model.value ?? RUNNING_MODEL);
    const saved = requested === RUNNING_MODEL ? "" : requested;
    const requestSaved = automatic ? "" : saved;
    const requestKey = automatic ? "automatic" : `selected:${saved}`;
    let request = discoveryRequests.get(requestKey);
    try {
        if (
            automatic &&
            automaticDiscoveryCache &&
            Date.now() - automaticDiscoveryCachedAt <= 1000
        ) {
            request = Promise.resolve(automaticDiscoveryCache);
        }
        if (!request) {
            request = (async () => {
                const response = await api.fetchApi(
                    `${DISCOVERY_ROUTE}?saved_model=${encodeURIComponent(requestSaved)}`,
                );
                const payload = await response.json();
                if (!response.ok) {
                    throw new Error(
                        payload?.error?.message ?? `HTTP ${response.status}`,
                    );
                }
                return payload;
            })();
            discoveryRequests.set(requestKey, request);
        }
        let payload = await request;
        if (automatic) {
            automaticDiscoveryCache = payload;
            automaticDiscoveryCachedAt = Date.now();
            payload = applySavedModelHint(payload, saved);
        }
        if (node.__llamacppGenerateState !== state) return;
        let discovery = reconcileDiscoverySelection(
            normalizeDiscovery(payload, saved),
            requested,
            String(state.model.value ?? RUNNING_MODEL),
        );
        if (automatic && saved) {
            discovery = {
                ...discovery,
                status: `${discovery.status} Refresh for selected passive properties.`,
            };
        }
        state.model.options ??= {};
        state.model.options.values = discovery.choices;
        state.model.value = discovery.selected;
        state.modelStatus.value = discovery.status;
    } catch (error) {
        state.modelStatus.value = `Managed discovery failed: ${error?.message ?? error}`;
        console.warn("[llama.cpp] Managed discovery failed", error);
    } finally {
        if (discoveryRequests.get(requestKey) === request) {
            discoveryRequests.delete(requestKey);
        }
        state.discoveryPending = false;
        fit(node);
        if (state.discoveryManualAfter) {
            state.discoveryManualAfter = false;
            void refreshDiscovery(node);
        }
    }
}

function scheduleDiscovery(node) {
    const state = node.__llamacppGenerateState;
    if (!state) return;
    if (state.discoveryTimer !== null) clearTimeout(state.discoveryTimer);
    state.discoveryTimer = setTimeout(() => {
        state.discoveryTimer = null;
        void refreshDiscovery(node, { automatic: true });
    }, 0);
}

function applySnapshot(node, incoming) {
    const state = node.__llamacppGenerateState;
    if (
        !state ||
        !snapshotTargetsNode(incoming, node, getRootGraph(), getWorkflowId())
    ) {
        return;
    }
    const next = reduceGenerationSnapshot(state.live, incoming);
    if (!next || next === state.live) return;
    state.live = next;
    state.liveStatus.value = formatGenerationStatus(next);
    state.response.value = previewText(next, "response");
    state.thinking.value = previewText(next, "thinking");
    const action = cancellationAction(next);
    state.cancel.name = action.label;
    state.cancel.label = action.label;
    state.cancel.disabled = !action.enabled;
    state.cancel.options.disabled = !action.enabled;
    node.graph?.setDirtyCanvas?.(true, false);
}

async function cancelGeneration(node) {
    const state = node.__llamacppGenerateState;
    const snapshot = state?.live;
    const action = cancellationAction(snapshot);
    if (!state || !snapshot || !action.enabled) return;
    state.cancel.disabled = true;
    state.cancel.options.disabled = true;
    try {
        const clientId = api.clientId ?? api.client_id;
        const result = await requestCancellation(snapshot, {
            fetchApi: api.fetchApi.bind(api),
            clientId,
            cancelRoute: CANCEL_ROUTE,
        });
        state.liveStatus.value = result.message;
    } catch (error) {
        state.liveStatus.value = `Stop failed: ${error?.message ?? error}`;
        const retry = cancellationRetryAction(state.live, snapshot);
        state.cancel.name = retry.label;
        state.cancel.label = retry.label;
        state.cancel.disabled = !retry.enabled;
        state.cancel.options.disabled = !retry.enabled;
        console.warn("[llama.cpp] Generation stop failed", error);
    }
}

function clearForExecution(node) {
    const state = node.__llamacppGenerateState;
    if (!state) return;
    state.live = null;
    state.liveStatus.value = "Starting...";
    state.response.value = "";
    state.thinking.value = "";
    state.cancel.disabled = true;
    state.cancel.options.disabled = true;
}

async function restoreActive({ force = false } = {}) {
    const clientId = api.clientId ?? api.client_id;
    const workflowId = getWorkflowId();
    if (
        !clientId ||
        (!force && restoredClientId === clientId && restoredWorkflowId === workflowId)
    ) {
        return;
    }
    if (restorePromise) {
        if (force) restoreAgain = true;
        return restorePromise;
    }
    restorePromise = (async () => {
        try {
            const response = await api.fetchApi(
                `${ACTIVE_ROUTE}?client_id=${encodeURIComponent(clientId)}`,
            );
            const payload = await response.json();
            if (!response.ok || !Array.isArray(payload?.executions)) return;
            if (getWorkflowId() !== workflowId) {
                restoreAgain = true;
                return;
            }
            restoredClientId = clientId;
            restoredWorkflowId = workflowId;
            restored.clear();
            for (const snapshot of payload.executions) {
                if (!snapshotMatchesWorkflow(snapshot, workflowId)) continue;
                rememberActiveSnapshot(restored, snapshot);
                for (const node of nodes) applySnapshot(node, snapshot);
            }
        } catch (error) {
            console.warn("[llama.cpp] Live generation restore failed", error);
        } finally {
            restorePromise = null;
            if (restoreAgain) {
                restoreAgain = false;
                void restoreActive({ force: true });
            }
        }
    })();
    return restorePromise;
}

export function setupGenerateNode(node, { refresh = true } = {}) {
    if (node.constructor?.comfyClass !== TARGET) return;
    if (node.__llamacppGenerateState) {
        nodes.add(node);
        installConfiguredReconciliation(node);
        installSampling(node);
        syncSampling(node);
        for (const prior of restored.values()) applySnapshot(node, prior);
        if (refresh) scheduleDiscovery(node);
        void restoreActive();
        return;
    }
    const model = widget(node, "model");
    if (!model) return;
    const modelStatus = readOnlyText(
        node,
        "Managed Model Facts",
        "Managed runtime facts have not been refreshed.",
    );
    const refreshButton = transient(
        node.addWidget("button", "Refresh Managed Models", null, () => refreshDiscovery(node)),
    );
    const liveStatus = readOnlyText(node, "Generation Status", "Idle");
    const response = readOnlyText(node, "Live Response", "", { multiline: true });
    const thinking = readOnlyText(node, "Live Thinking", "", { multiline: true });
    const cancel = transient(
        node.addWidget("button", "Stopping unavailable", null, () => cancelGeneration(node)),
    );
    cancel.disabled = true;
    cancel.options.disabled = true;
    node.__llamacppGenerateState = {
        cancel,
        discoveryManualAfter: false,
        discoveryPending: false,
        discoveryTimer: null,
        live: null,
        liveStatus,
        model,
        modelStatus,
        refreshButton,
        response,
        thinking,
    };
    nodes.add(node);
    const originalRemoved = node.onRemoved;
    node.onRemoved = function (...args) {
        nodes.delete(this);
        const state = this.__llamacppGenerateState;
        if (state?.discoveryTimer !== null) clearTimeout(state.discoveryTimer);
        return originalRemoved?.apply(this, args);
    };
    installConfiguredReconciliation(node);
    installSampling(node);
    for (const prior of restored.values()) applySnapshot(node, prior);
    fit(node);
    if (refresh) scheduleDiscovery(node);
    void restoreActive();
}

app.registerExtension({
    name: "llamacpp.GenerateUX",
    beforeConfigureGraph() {
        nodes.clear();
        restored.clear();
        automaticDiscoveryCache = null;
        automaticDiscoveryCachedAt = 0;
        restoredClientId = null;
        restoredWorkflowId = null;
        restoreAgain = restorePromise !== null;
    },
    setup() {
        api.addEventListener(LIVE_EVENT_NAME, (event) => {
            const snapshot = event?.detail ?? {};
            if (!snapshotMatchesWorkflow(snapshot, getWorkflowId())) return;
            for (const node of nodes) applySnapshot(node, snapshot);
            rememberActiveSnapshot(restored, snapshot);
        });
        api.addEventListener("executing", (event) => {
            const detail = event?.detail;
            const display = detail?.display_node ?? detail?.node ?? detail;
            if (display == null) return;
            const displayId = String(display);
            for (const [key, snapshot] of restored) {
                if (
                    snapshotMatchesWorkflow(snapshot, getWorkflowId()) &&
                    snapshot.display_node_id === displayId
                ) {
                    restored.delete(key);
                }
            }
            const target = resolveExecutionNode(getRootGraph(), displayId);
            if (target && nodes.has(target)) clearForExecution(target);
        });
        api.addEventListener("status", () => void restoreActive());
        api.addEventListener("reconnected", () => {
            restoredClientId = null;
            restoredWorkflowId = null;
            void restoreActive({ force: true });
        });
        void restoreActive();
    },
    nodeCreated(node) {
        setupGenerateNode(node);
    },
    loadedGraphNode(node) {
        setupGenerateNode(node);
    },
});
