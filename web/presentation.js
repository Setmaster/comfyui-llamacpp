import { app } from "../../scripts/app.js";
import {
    ADVANCED_NODE_IDS,
    setupAdvancedWidgetCompatibility,
} from "./advanced_widgets.js";

function isTarget(node) {
    return ADVANCED_NODE_IDS.has(node.constructor?.comfyClass);
}

app.registerExtension({
    name: "llamacpp.AdvancedWidgets",
    nodeCreated(node) {
        if (isTarget(node)) setupAdvancedWidgetCompatibility(node, { fit: true });
    },
    loadedGraphNode(node) {
        if (isTarget(node)) setupAdvancedWidgetCompatibility(node);
    },
});
