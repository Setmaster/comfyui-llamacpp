import { app } from "../../scripts/app.js";
import { ComfyWidgets } from "../../scripts/widgets.js";

function setupStatusOutput(node) {
    if (node.constructor?.comfyClass !== "LlamaCppServerStatus") return;
    if (node.__llamacppStatusWidget) return;

    const widget = ComfyWidgets.STRING(
        node,
        "diagnostics",
        ["STRING", { multiline: true, default: "Run this node to refresh diagnostics." }],
        app,
    ).widget;
    widget.options ??= {};
    widget.options.serialize = false;
    widget.serialize = false;
    widget.value = widget.value ?? "Run this node to refresh diagnostics.";
    const element = "element" in widget ? widget.element : widget.inputEl;
    if (element) element.readOnly = true;
    node.__llamacppStatusWidget = widget;

    const originalOnExecuted = node.onExecuted;
    node.onExecuted = function (message) {
        originalOnExecuted?.call(this, message);
        const text = message?.text;
        widget.value = Array.isArray(text) ? (text[0] ?? "") : (text ?? "");
    };
}

app.registerExtension({
    name: "llamacpp.ServerStatusOutput",
    nodeCreated: setupStatusOutput,
    loadedGraphNode: setupStatusOutput,
});
