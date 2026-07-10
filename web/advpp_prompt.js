import { app } from "../../scripts/app.js";
import { applyTemplateValues } from "./template_utils.js";

let templatesPromise;

async function loadTemplates() {
    if (!templatesPromise) {
        const url = new URL("./templates.json", import.meta.url);
        templatesPromise = fetch(url)
            .then((response) => {
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                return response.json();
            })
            .catch((error) => {
                console.warn("[llama.cpp] Could not load templates.json", error);
                return { Empty: { system_prompt: "", prompt: "" } };
            });
    }
    return templatesPromise;
}

function setupTemplateWidget(node) {
    if (node.constructor?.comfyClass !== "LlamaCppAdvPPPrompt") return;
    const template = node.widgets?.find((widget) => widget.name === "template");
    if (!template || template.__llamacppTemplates) return;

    template.__llamacppTemplates = true;
    const originalCallback = template.callback;
    template.callback = async function (value, ...args) {
        const templates = await loadTemplates();
        applyTemplateValues(node.widgets ?? [], templates[value]);
        app.graph?.setDirtyCanvas?.(true, true);
        return originalCallback?.call(this, value, ...args);
    };
}

app.registerExtension({
    name: "llamacpp.Templates",
    init: loadTemplates,
    nodeCreated: setupTemplateWidget,
    loadedGraphNode: setupTemplateWidget,
});
