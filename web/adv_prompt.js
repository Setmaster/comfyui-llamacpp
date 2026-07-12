import { app } from "../../scripts/app.js";
import { setupDynamicImageInputs } from "./dynamic_images.js";

const TARGETS = new Set(["LlamaCppAdvPrompt", "LlamaCppAdvPPPrompt", "LlamaCppGenerate"]);

function setup(node) {
    if (!TARGETS.has(node.constructor?.comfyClass)) return;
    setupDynamicImageInputs(node, app);
}

app.registerExtension({
    name: "llamacpp.DynamicImages",
    nodeCreated: setup,
    loadedGraphNode: setup,
});
