const DECLARED_ADVANCED = "__llamacppDeclaredAdvanced";
const INSTALLED = "__llamacppAdvancedWidgetsInstalled";

export const ADVANCED_NODE_IDS = new Set([
    "StartLlamaCppServer",
    "LlamaCppConnection",
    "StartLlamaCppRouter",
    "LlamaCppListModels",
    "LlamaCppLoadModel",
    "LlamaCppUnloadModel",
    "LlamaCppBasicPrompt",
    "LlamaCppAdvPrompt",
    "LlamaCppAdvPPPrompt",
    "LlamaCppPromptOutput",
    "LlamaCppTokenCount",
    "LlamaCppModelInfo",
    "LlamaCppStructuredOutput",
]);

function setDeclaredAdvanced(widget, value) {
    if (!(DECLARED_ADVANCED in widget)) {
        Object.defineProperty(widget, DECLARED_ADVANCED, {
            configurable: true,
            value: Boolean(value),
            writable: true,
        });
    }
    return widget[DECLARED_ADVANCED];
}

function inputForWidget(node, widget) {
    const direct = node.getSlotFromWidget?.(widget);
    if (direct != null) return direct;
    return (node.inputs ?? []).find(
        (input) => input.widget?.name === widget.name || input.name === widget.name,
    );
}

function widgetIsLinked(node, widget) {
    return inputForWidget(node, widget)?.link != null;
}

function setLinkedWidgetAdvanced(widget, advanced) {
    widget.advanced = advanced;
    widget.options ??= {};
    widget.options.advanced = advanced;
}

/**
 * Mirror schema `options.advanced` onto classic LiteGraph widget instances.
 * Advanced widgets converted to linked inputs stay visible, together with any
 * companion widgets such as ComfyUI's control-after-generate seed selector.
 */
export function syncAdvancedWidgets(node) {
    const widgets = node.widgets ?? [];
    const linkedWidgets = new Set(
        widgets.flatMap((widget) =>
            Array.isArray(widget.linkedWidgets) ? widget.linkedWidgets : [],
        ),
    );

    for (const widget of widgets) {
        if (linkedWidgets.has(widget)) continue;

        const declared = setDeclaredAdvanced(
            widget,
            widget.options?.advanced === true || widget.advanced === true,
        );
        const effectiveAdvanced = declared && !widgetIsLinked(node, widget);
        widget.advanced = effectiveAdvanced;

        for (const linkedWidget of widget.linkedWidgets ?? []) {
            setDeclaredAdvanced(
                linkedWidget,
                declared ||
                    linkedWidget.options?.advanced === true ||
                    linkedWidget.advanced === true,
            );
            setLinkedWidgetAdvanced(linkedWidget, effectiveAdvanced);
        }
    }
}

/** Fit a newly created node to the widgets that are currently visible. */
export function fitNodeToVisibleWidgets(node) {
    const current = node.size ?? [320, 100];
    const computed = node.computeSize?.();
    if (!Array.isArray(computed) || computed.length < 2) return;
    node.setSize?.([Math.max(current[0], computed[0]), computed[1]]);
}

function expandNodeToVisibleWidgets(node) {
    if (typeof node.expandToFitContent === "function") {
        node.expandToFitContent();
        return;
    }
    const current = node.size ?? [320, 100];
    const computed = node.computeSize?.();
    if (!Array.isArray(computed) || computed.length < 2) return;
    node.setSize?.([
        Math.max(current[0], computed[0]),
        Math.max(current[1], computed[1]),
    ]);
}

/**
 * Install classic-renderer compatibility on one node instance.  Loaded graph
 * nodes intentionally keep their serialized size; freshly created nodes can be
 * fitted by passing `fit: true`.
 */
export function setupAdvancedWidgetCompatibility(node, { fit = false } = {}) {
    syncAdvancedWidgets(node);

    if (!node[INSTALLED]) {
        Object.defineProperty(node, INSTALLED, {
            configurable: true,
            value: true,
        });

        const originalGetLayoutWidgets = node.getLayoutWidgets;
        if (typeof originalGetLayoutWidgets === "function") {
            node.getLayoutWidgets = function (...args) {
                return originalGetLayoutWidgets
                    .apply(this, args)
                    .filter((widget) => !widget.advanced || this.showAdvanced);
            };
        }

        const originalOnConnectionsChange = node.onConnectionsChange;
        node.onConnectionsChange = function (...args) {
            const result = originalOnConnectionsChange?.apply(this, args);
            syncAdvancedWidgets(this);
            expandNodeToVisibleWidgets(this);
            this.graph?.setDirtyCanvas?.(true, true);
            return result;
        };
    }

    if (fit) fitNodeToVisibleWidgets(node);
}
