import { app } from "../../scripts/app.js";
import { ComfyWidgets } from "../../scripts/widgets.js";

function setupOutput(node) {
    if (node.constructor?.comfyClass !== "LlamaCppPromptOutput") return;
    if (node.__llamacppOutputWidget) return;

    const widget = ComfyWidgets.STRING(
        node,
        "output",
        ["STRING", { multiline: true, default: "" }],
        app,
    ).widget;
    widget.options ??= {};
    widget.options.serialize = false;
    widget.serialize = false;
    widget.value = widget.value ?? "";
    node.__llamacppOutputWidget = widget;

    const originalOnExecuted = node.onExecuted;
    node.onExecuted = function (message) {
        originalOnExecuted?.call(this, message);
        const text = message?.text;
        widget.value = Array.isArray(text) ? (text[0] ?? "") : (text ?? "");
    };
}

app.registerExtension({
    name: "llamacpp.PromptOutput",
    nodeCreated: setupOutput,
    loadedGraphNode: setupOutput,
});
