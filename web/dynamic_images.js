export const MAX_IMAGES = 10;

export function normalizeImageCount(value) {
    const numeric = Number(value ?? 2);
    if (!Number.isFinite(numeric)) return 2;
    return Math.max(0, Math.min(MAX_IMAGES, Math.trunc(numeric)));
}

function imageNumber(input) {
    const match = /^image_(\d+)$/.exec(input?.name ?? "");
    return match ? Number(match[1]) : null;
}

export function syncImageInputs(node, requestedCount) {
    const count = normalizeImageCount(requestedCount);
    const existing = (node.inputs ?? [])
        .map((input, index) => ({ input, index, number: imageNumber(input) }))
        .filter((entry) => entry.number !== null);

    for (const entry of [...existing].reverse()) {
        if (entry.number > count) {
            const currentIndex = node.inputs?.findIndex(
                (input) => input.name === entry.input.name,
            );
            if (currentIndex >= 0) node.removeInput(currentIndex);
        }
    }

    const names = new Set((node.inputs ?? []).map((input) => input.name));
    for (let index = 1; index <= count; index += 1) {
        const name = `image_${index}`;
        if (!names.has(name)) node.addInput(name, "IMAGE");
    }

    const currentSize = node.size ?? [320, 100];
    const minimum = node.computeSize?.() ?? currentSize;
    node.setSize?.([
        Math.max(currentSize[0], minimum[0]),
        Math.max(currentSize[1], minimum[1]),
    ]);
    return count;
}

export function setupDynamicImageInputs(node, app) {
    const widget = node.widgets?.find((candidate) => candidate.name === "image_amount");
    if (!widget) return;

    syncImageInputs(node, widget.value ?? 2);

    if (widget.__llamacppDynamicImages) return;
    widget.__llamacppDynamicImages = true;
    const originalCallback = widget.callback;
    widget.callback = function (value, ...args) {
        syncImageInputs(node, value);
        app.graph?.setDirtyCanvas?.(true, true);
        return originalCallback?.call(this, value, ...args);
    };
}
