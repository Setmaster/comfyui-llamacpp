function isObject(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
}

function templateFields(template, name = "selected template") {
    if (!isObject(template)) {
        throw new TypeError(`Template ${name} must be an object`);
    }

    const systemPrompt = Object.hasOwn(template, "system_prompt") ? template.system_prompt : "";
    const prompt = Object.hasOwn(template, "prompt") ? template.prompt : "";
    if (typeof systemPrompt !== "string" || typeof prompt !== "string") {
        throw new TypeError(`Template ${name} fields must be strings`);
    }
    return { system_prompt: systemPrompt, prompt };
}

function promptWidgets(widgets) {
    const systemPrompt = widgets.find((widget) => widget.name === "system_prompt");
    const prompt = widgets.find((widget) => widget.name === "prompt");
    return systemPrompt && prompt ? { systemPrompt, prompt } : null;
}

function graphFor(node, appRef) {
    return node.graph ?? appRef?.graph;
}

function markDirty(node, appRef) {
    graphFor(node, appRef)?.setDirtyCanvas?.(true, true);
}

export function normalizeTemplates(value) {
    if (!isObject(value)) {
        throw new TypeError("Template file must contain an object");
    }

    const entries = [];
    let hasEmpty = false;
    for (const [name, template] of Object.entries(value)) {
        if (!name) {
            throw new TypeError("Template names must be non-empty strings");
        }
        entries.push([name, templateFields(template, JSON.stringify(name))]);
        hasEmpty ||= name === "Empty";
    }
    if (!hasEmpty) {
        entries.unshift(["Empty", { system_prompt: "", prompt: "" }]);
    }
    return Object.fromEntries(entries);
}

export function applyTemplateValues(widgets, template, { replace = false } = {}) {
    const fields = promptWidgets(widgets);
    if (!fields) return false;

    let values;
    try {
        values = templateFields(template);
    } catch {
        return false;
    }

    let changed = false;
    if (replace || fields.systemPrompt.value === "") {
        if (fields.systemPrompt.value !== values.system_prompt) {
            fields.systemPrompt.value = values.system_prompt;
            changed = true;
        }
    }
    if (replace || fields.prompt.value === "") {
        if (fields.prompt.value !== values.prompt) {
            fields.prompt.value = values.prompt;
            changed = true;
        }
    }
    return changed;
}

export function setupTemplateWidget(node, { appRef, loadTemplates, logger = console } = {}) {
    if (node.constructor?.comfyClass !== "LlamaCppAdvPPPrompt") return false;
    if (typeof loadTemplates !== "function") return false;

    const template = node.widgets?.find((widget) => widget.name === "template");
    if (!template || template.__llamacppTemplates) return false;

    template.__llamacppTemplates = true;
    template.__llamacppTemplateRevision = 0;
    const originalCallback = template.callback;
    template.callback = async function (value, ...args) {
        const revision = ++template.__llamacppTemplateRevision;
        const originalResult = originalCallback?.call(this, value, ...args);
        try {
            const templates = await loadTemplates();
            if (revision !== template.__llamacppTemplateRevision || template.value !== value) {
                return originalResult;
            }
            if (applyTemplateValues(node.widgets ?? [], templates[value])) {
                markDirty(node, appRef);
            }
        } catch (error) {
            logger?.warn?.("[llama.cpp] Could not apply selected template", error);
        }
        return originalResult;
    };

    const replaceWidget = node.addWidget?.(
        "button",
        "Replace / Reset from Template",
        "Replace / Reset",
        async () => {
            const selected = template.value;
            try {
                const templates = await loadTemplates();
                if (template.value !== selected) return false;
                const selectedTemplate = templates[selected];
                const fields = promptWidgets(node.widgets ?? []);
                if (!fields || !selectedTemplate) return false;

                const values = templateFields(selectedTemplate);
                if (
                    fields.systemPrompt.value === values.system_prompt &&
                    fields.prompt.value === values.prompt
                ) {
                    return false;
                }

                const graph = graphFor(node, appRef);
                graph?.beforeChange?.(node);
                try {
                    applyTemplateValues(node.widgets ?? [], selectedTemplate, { replace: true });
                } finally {
                    graph?.afterChange?.(node);
                }
                markDirty(node, appRef);
                return true;
            } catch (error) {
                logger?.warn?.("[llama.cpp] Could not replace prompt fields from template", error);
                return false;
            }
        },
        { serialize: false },
    );
    if (replaceWidget) {
        replaceWidget.options ??= {};
        replaceWidget.options.serialize = false;
        replaceWidget.serialize = false;
        replaceWidget.__llamacppTemplateAction = true;
    }
    return true;
}
