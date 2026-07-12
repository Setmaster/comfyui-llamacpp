import { app } from "../../scripts/app.js";
import { normalizeTemplates, setupTemplateWidget } from "./template_utils.js";

let templatesPromise;

async function loadTemplates() {
    if (!templatesPromise) {
        const url = new URL("./templates.json", import.meta.url);
        templatesPromise = fetch(url)
            .then((response) => {
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                return response.json();
            })
            .then(normalizeTemplates)
            .catch((error) => {
                console.warn("[llama.cpp] Could not load templates.json", error);
                return normalizeTemplates({});
            });
    }
    return templatesPromise;
}

function setup(node) {
    setupTemplateWidget(node, { appRef: app, loadTemplates });
}

app.registerExtension({
    name: "llamacpp.Templates",
    init: loadTemplates,
    nodeCreated: setup,
    loadedGraphNode: setup,
});
