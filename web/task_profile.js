import { api } from "../../scripts/api.js";
import { app } from "../../scripts/app.js";
import {
    installPostConfigureReconciliation,
    markWidgetReadOnly,
} from "./generate_controls.js";
import {
    canonicalProfileJSON,
    createAutomaticProfileLoader,
    normalizeProfilesResponse,
    parseProfileSnapshot,
    profileSnapshotStatus,
    reconcileProfileSelection,
} from "./task_profile_state.js";

const TARGET = "LlamaCppTaskProfile";
const ROUTE = "/llamacpp/profiles";

const loadProfiles = createAutomaticProfileLoader(async () => {
    const response = await api.fetchApi(ROUTE);
    const payload = await response.json();
    if (!response.ok) {
        throw new Error(payload?.error?.message ?? `HTTP ${response.status}`);
    }
    const profiles = normalizeProfilesResponse(payload);
    if (!profiles.length) throw new Error("No valid profiles were returned");
    return profiles;
});

function transient(widget) {
    widget.options ??= {};
    widget.options.serialize = false;
    widget.serialize = false;
    widget.serializeValue = () => undefined;
    return widget;
}

function hideSerializedSnapshot(widget) {
    if (!widget || widget.__llamacppProfileSnapshotHidden) return;
    widget.__llamacppProfileSnapshotHidden = true;
    widget.options ??= {};
    widget.options.hidden = true;
    widget.hidden = true;
    widget.type = "hidden";
    widget.computeSize = () => [0, -4];
}

function reconcileConfiguredSnapshot(node) {
    const state = node.__llamacppProfileState;
    const saved = parseProfileSnapshot(state?.snapshot.value);
    if (!state || !saved) return;
    if (!state.profilesFetched) {
        state.profiles = [saved];
        state.selector.options.values = [saved.id];
    }
    state.selector.value = reconcileProfileSelection(
        state.snapshot.value,
        state.profiles,
        state.selector.value,
        state.selectorTouched,
    );
    setStatus(node);
}

function installConfiguredReconciliation(node) {
    installPostConfigureReconciliation(
        node,
        "__llamacppProfilePostConfigure",
        (configured) => {
            const state = configured.__llamacppProfileState;
            if (state) state.selectorTouched = false;
            reconcileConfiguredSnapshot(configured);
            fit(configured);
        },
    );
}

function scheduleProfileRefresh(node) {
    const state = node.__llamacppProfileState;
    if (!state) return;
    if (state.refreshTimer !== null) clearTimeout(state.refreshTimer);
    state.refreshTimer = setTimeout(() => {
        state.refreshTimer = null;
        void refreshProfiles(node, { automatic: true });
    }, 0);
}

function fit(node) {
    const current = node.size ?? [320, 100];
    const computed = node.computeSize?.() ?? current;
    node.setSize?.([
        Math.max(360, current[0], computed[0]),
        Math.max(current[1], computed[1]),
    ]);
    node.graph?.setDirtyCanvas?.(true, true);
}

function setStatus(node) {
    const state = node.__llamacppProfileState;
    if (!state) return;
    const status = profileSnapshotStatus(state.snapshot.value, state.profiles);
    const selected = state.profiles.find((profile) => profile.id === state.selector.value);
    const suffix = selected && selected.id !== parseProfileSnapshot(state.snapshot.value)?.id
        ? `; selected ${selected.name} is not saved yet`
        : "";
    state.status.value = `${status.text}${suffix}`;
    node.graph?.setDirtyCanvas?.(true, false);
}

async function refreshProfiles(node, { automatic = false } = {}) {
    const state = node.__llamacppProfileState;
    if (!state) return;
    if (state.refreshing) {
        if (!automatic && state.refreshingAutomatic) state.refreshManualAfter = true;
        return;
    }
    state.refreshing = true;
    state.refreshingAutomatic = automatic;
    state.status.value = "Refreshing local profiles...";
    try {
        const profiles = await loadProfiles({ automatic });
        state.profiles = profiles;
        state.profilesFetched = true;
        state.selector.options.values = profiles.map((profile) => profile.id);
        if (!profiles.some((profile) => profile.id === state.selector.value)) {
            const snapshot = parseProfileSnapshot(state.snapshot.value);
            state.selector.value = profiles.some((profile) => profile.id === snapshot?.id)
                ? snapshot.id
                : profiles[0].id;
        }
        setStatus(node);
    } catch (error) {
        state.status.value = `Profile refresh failed: ${error?.message ?? error}`;
        console.warn("[llama.cpp] Profile refresh failed", error);
    } finally {
        state.refreshing = false;
        state.refreshingAutomatic = false;
        fit(node);
        if (state.refreshManualAfter) {
            state.refreshManualAfter = false;
            void refreshProfiles(node);
        }
    }
}

function updateSnapshot(node) {
    const state = node.__llamacppProfileState;
    const selected = state?.profiles.find((profile) => profile.id === state.selector.value);
    const encoded = canonicalProfileJSON(selected);
    if (!state || !encoded || encoded === state.snapshot.value) {
        setStatus(node);
        return;
    }
    const graph = node.graph ?? app.graph;
    graph?.beforeChange?.();
    try {
        state.snapshot.value = encoded;
    } finally {
        graph?.afterChange?.();
    }
    graph?.setDirtyCanvas?.(true, true);
    setStatus(node);
}

export function setupTaskProfileNode(node, { refresh = true } = {}) {
    if (node.constructor?.comfyClass !== TARGET) return;
    if (node.__llamacppProfileState) {
        installConfiguredReconciliation(node);
        hideSerializedSnapshot(node.__llamacppProfileState.snapshot);
        reconcileConfiguredSnapshot(node);
        fit(node);
        if (refresh) scheduleProfileRefresh(node);
        return;
    }
    const snapshot = node.widgets?.find((widget) => widget.name === "profile_snapshot");
    if (!snapshot) return;
    hideSerializedSnapshot(snapshot);
    const saved = parseProfileSnapshot(snapshot.value);
    const selector = transient(
        node.addWidget(
            "combo",
            "Local Profile",
            saved?.id ?? "freeform",
            () => {
                const state = node.__llamacppProfileState;
                if (state) state.selectorTouched = true;
                setStatus(node);
            },
            { values: [saved?.id ?? "freeform"] },
        ),
    );
    const refreshButton = transient(
        node.addWidget("button", "Refresh Profiles", null, () => refreshProfiles(node)),
    );
    const updateButton = transient(
        node.addWidget("button", "Update Saved Snapshot", null, () => updateSnapshot(node)),
    );
    const status = markWidgetReadOnly(transient(
        node.addWidget("text", "Profile Status", "Loading local profiles...", null, {
            multiline: true,
            read_only: true,
        }),
    ));
    node.__llamacppProfileState = {
        profiles: saved ? [saved] : [],
        profilesFetched: false,
        refreshManualAfter: false,
        refreshing: false,
        refreshingAutomatic: false,
        refreshTimer: null,
        refreshButton,
        selector,
        snapshot,
        status,
        updateButton,
        selectorTouched: false,
    };
    const originalRemoved = node.onRemoved;
    node.onRemoved = function (...args) {
        const state = this.__llamacppProfileState;
        if (state?.refreshTimer !== null) clearTimeout(state.refreshTimer);
        return originalRemoved?.apply(this, args);
    };
    installConfiguredReconciliation(node);
    setStatus(node);
    fit(node);
    if (refresh) scheduleProfileRefresh(node);
}

app.registerExtension({
    name: "llamacpp.TaskProfiles",
    nodeCreated(node) {
        setupTaskProfileNode(node);
    },
    loadedGraphNode(node) {
        if (node.__llamacppProfileState) {
            node.__llamacppProfileState.selectorTouched = false;
        }
        setupTaskProfileNode(node);
    },
});
