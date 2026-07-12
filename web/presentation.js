import { app } from "../../scripts/app.js";
import {
    ADVANCED_NODE_IDS,
    setupAdvancedWidgetCompatibility,
} from "./advanced_widgets.js";
import { migrateLegacyWorkflowData } from "./workflow_compat.js";

function isTarget(node) {
    return ADVANCED_NODE_IDS.has(node.constructor?.comfyClass);
}

app.registerExtension({
    name: "llamacpp.AdvancedWidgets",
    beforeConfigureGraph: migrateLegacyWorkflowData,
    nodeCreated(node) {
        if (isTarget(node)) setupAdvancedWidgetCompatibility(node, { fit: true });
    },
    loadedGraphNode(node) {
        if (isTarget(node)) setupAdvancedWidgetCompatibility(node);
    },
});
