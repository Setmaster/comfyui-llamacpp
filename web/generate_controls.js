export const RUNNING_MODEL = "(use running model)";
export const EXPERT_SAMPLERS = Object.freeze([
    "temperature",
    "top_p",
    "top_k",
    "min_p",
    "repeat_penalty",
    "presence_penalty",
    "frequency_penalty",
]);

/** Keep output/status widgets copyable while preventing accidental edits. */
export function markWidgetReadOnly(widget) {
    if (!widget) return widget;
    widget.options ??= {};
    widget.options.read_only = true;
    const element = widget.element ?? widget.inputEl;
    if (element) element.readOnly = true;
    return widget;
}

/**
 * Run one reconciliation after LiteGraph has restored serialized widget values.
 * `nodeCreated` runs before `configure()` for clone/paste operations, while
 * `loadedGraphNode` is not emitted for them.
 */
export function installPostConfigureReconciliation(node, marker, reconcile) {
    if (!node || typeof marker !== "string" || !marker || typeof reconcile !== "function") {
        return false;
    }
    if (node[marker]) return false;
    Object.defineProperty(node, marker, {
        configurable: true,
        value: true,
    });
    const original = node.onConfigure;
    node.onConfigure = function (...args) {
        const result = original?.apply(this, args);
        reconcile(this, ...args);
        return result;
    };
    return true;
}

function text(value, maximum = 4096) {
    return typeof value === "string" && value.length <= maximum ? value : "";
}

function factValue(fact, render) {
    if (fact?.state !== "known") return "Unknown";
    return render(fact.value);
}

function selectedFacts(models, savedModel) {
    const selected = savedModel
        ? models.find(
              (model) =>
                  text(model?.model_id) === savedModel ||
                  (Array.isArray(model?.aliases) && model.aliases.includes(savedModel)),
          )
        : models.length === 1
          ? models[0]
          : null;
    if (!selected) return "";
    const residency = text(selected.residency, 64) || "unknown";
    const context = factValue(selected.context_length, (value) =>
        Number.isSafeInteger(value) && value > 0 ? String(value) : "Unknown",
    );
    const image = factValue(selected.input_capabilities?.image, (value) =>
        value === true ? "Yes" : value === false ? "No" : "Unknown",
    );
    const projector = text(selected.projector?.projector_name, 512);
    const projectorStatus = !projector
        ? ""
        : selected.projector?.requires_confirmation === false
          ? `; projector ${projector} (configured)`
          : `; projector suggestion ${projector} (confirm)`;
    return `; residency ${residency}; context ${context}; image ${image}${projectorStatus}`;
}

export function isCustomSampling(value) {
    return String(value ?? "").toLowerCase() === "custom";
}

export function shouldShowExpertSampling(value, samplingModeLinked = false) {
    return isCustomSampling(value) || samplingModeLinked === true;
}

export function applySavedModelHint(value, savedModel) {
    if (!savedModel || !value || value.schema_version !== 1 || !Array.isArray(value.models)) {
        return value;
    }
    const available = value.models.some(
        (model) =>
            text(model?.model_id) === savedModel ||
            (Array.isArray(model?.aliases) && model.aliases.includes(savedModel)),
    );
    return {
        ...value,
        saved_model: savedModel,
        saved_model_state: available ? "available" : "missing",
    };
}

export function normalizeDiscovery(value, savedModel = "") {
    if (!value || value.schema_version !== 1 || !Array.isArray(value.models)) {
        return {
            choices: [RUNNING_MODEL, ...(savedModel ? [savedModel] : [])],
            status: "Managed runtime discovery returned invalid data",
            state: "unknown",
        };
    }
    const seen = new Set([RUNNING_MODEL]);
    const choices = [RUNNING_MODEL];
    const models = value.models.slice(0, 4096);
    for (const model of models) {
        const modelId = text(model?.model_id);
        if (modelId && !seen.has(modelId)) {
            seen.add(modelId);
            choices.push(modelId);
        }
    }
    if (savedModel && !seen.has(savedModel)) choices.push(savedModel);
    const state = text(value.saved_model_state, 32) || "unknown";
    const ownership = value.owned === true ? "owned" : value.mode === "none" ? "offline" : "attached";
    const warning = Array.isArray(value.warnings) && value.warnings.length
        ? ` ${text(value.warnings[0], 512)}`
        : "";
    const facts = selectedFacts(models, savedModel);
    const status = savedModel
        ? `Managed discovery: ${savedModel} is ${state} (${ownership})${facts}.${warning}`
        : `Managed discovery: ${choices.length - 1} model(s) (${ownership})${facts}.${warning}`;
    return { choices, state, status };
}

export function reconcileDiscoverySelection(discovery, requestedModel, currentModel) {
    const requested = String(requestedModel ?? RUNNING_MODEL);
    const current = String(currentModel ?? RUNNING_MODEL);
    const choices = [...discovery.choices];
    if (!choices.includes(current)) choices.push(current);
    if (current === requested) {
        return { ...discovery, choices, selected: current };
    }
    return {
        ...discovery,
        choices,
        selected: current,
        status: `${discovery.status} Selection changed during Refresh; refresh again for its facts.`,
    };
}

export function formatGenerationStatus(snapshot) {
    if (!snapshot) return "Idle";
    const phase = text(snapshot.phase, 64) || "unknown";
    const elapsed = Number.isFinite(snapshot.elapsed_ms)
        ? `${(Math.max(0, snapshot.elapsed_ms) / 1000).toFixed(1)}s`
        : "";
    const promptPercent = snapshot.prompt_progress?.percent;
    const progress = Number.isFinite(promptPercent)
        ? `, prompt ${Math.max(0, Math.min(100, promptPercent)).toFixed(1)}%`
        : "";
    const partial = snapshot.partial ? ", partial output" : "";
    const release = snapshot.release?.status ? `, release ${text(snapshot.release.status, 64)}` : "";
    const cleanup = snapshot.stream_cleanup;
    const cleanupStatus = Number.isSafeInteger(cleanup?.attempts) &&
        cleanup.attempts > 0 &&
        cleanup.confirmed !== true
        ? ", stream cleanup unconfirmed"
        : "";
    const error = snapshot.error?.message ? `: ${text(snapshot.error.message, 512)}` : "";
    return `${phase}${
        elapsed ? ` (${elapsed}` + `${progress}${partial}${release}${cleanupStatus})` : ""
    }${error}`;
}
