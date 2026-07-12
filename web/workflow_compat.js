const LEGACY_OUTPUT_PROPERTY = "__llamacppLegacyPromptOutput";

const LEGACY_SEED_LAYOUTS = Object.freeze({
    LlamaCppBasicPrompt: { booleanTail: 2, seedIndex: 11, widgetCount: 14 },
    LlamaCppAdvPrompt: { booleanTail: 2, seedIndex: 14, widgetCount: 17 },
    LlamaCppAdvPPPrompt: { booleanTail: 3, seedIndex: 15, widgetCount: 19 },
});

function migrateSeedCompanion(node) {
    const layout = LEGACY_SEED_LAYOUTS[node?.type];
    const values = node?.widgets_values;
    if (!layout || !Array.isArray(values) || values.length !== layout.widgetCount) return false;
    if (typeof values[layout.seedIndex] !== "number") return false;

    const legacyTail = values.slice(layout.seedIndex + 1);
    if (
        legacyTail.length !== layout.booleanTail ||
        legacyTail.some((value) => typeof value !== "boolean")
    ) {
        return false;
    }

    // Historical workflows had no companion, so their numeric seed stayed
    // unchanged across queues. Current Comfy mutates it unless this is fixed.
    values.splice(layout.seedIndex + 1, 0, "fixed");
    return true;
}

function migratePromptOutput(node) {
    const values = node?.widgets_values;
    if (
        node?.type !== "LlamaCppPromptOutput" ||
        !Array.isArray(values) ||
        values.length !== 2 ||
        typeof values[0] !== "boolean" ||
        typeof values[1] !== "string"
    ) {
        return false;
    }

    node.properties ??= {};
    node.properties[LEGACY_OUTPUT_PROPERTY] = values[1];
    node.widgets_values = [values[0]];
    return true;
}

/**
 * Normalize only the exact widget layouts serialized by the 0.2.1 frontend.
 * Current workflows already contain Comfy's seed companion and do not match
 * these guards.
 */
export function migrateLegacyWorkflowData(workflow) {
    let promptOutputs = 0;
    let seedCompanions = 0;
    for (const node of workflow?.nodes ?? []) {
        seedCompanions += Number(migrateSeedCompanion(node));
        promptOutputs += Number(migratePromptOutput(node));
    }
    return { promptOutputs, seedCompanions };
}

/** Restore a historical display value once, then remove its temporary marker. */
export function restoreLegacyPromptOutput(node, widget) {
    const properties = node?.properties;
    if (!properties || typeof properties[LEGACY_OUTPUT_PROPERTY] !== "string") return false;
    widget.value = properties[LEGACY_OUTPUT_PROPERTY];
    delete properties[LEGACY_OUTPUT_PROPERTY];
    return true;
}
