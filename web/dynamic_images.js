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

function graphLink(node, linkId) {
    const privateLinks = node.graph?._links;
    if (privateLinks?.get) {
        return privateLinks.get(linkId) ?? privateLinks.get(String(linkId));
    }
    const links = node.graph?.links;
    if (links?.get) return links.get(linkId) ?? links.get(String(linkId));
    return links?.[linkId] ?? links?.[String(linkId)] ?? null;
}

function inputLinks(node, startIndex) {
    return (node.inputs ?? []).slice(startIndex).map((input, offset) => ({
        input,
        link: typeof node.getInputLink === "function"
            ? node.getInputLink(startIndex + offset)
            : graphLink(node, input.link),
    }));
}

function repairTargetSlots(node, assignments) {
    // Input.link is derived from the current numeric slot on newer frontends.
    // Retain each input's original link before moving it, and move the highest
    // targets first so the link store never sees two links at one destination.
    for (const { input, link } of [...assignments].reverse()) {
        const index = node.inputs.indexOf(input);
        if (!link || index < 0) continue;
        if (typeof link.updateEndpoints === "function") {
            link.updateEndpoints({ targetSlot: index });
        } else {
            link.target_slot = index;
        }
    }
}

function addInputBefore(node, name, type, options, beforeInputName) {
    node.addInput(name, type, options);
    if (!beforeInputName) return;
    const currentIndex = node.inputs?.findIndex((input) => input.name === name) ?? -1;
    const beforeIndex = node.inputs?.findIndex((input) => input.name === beforeInputName) ?? -1;
    if (currentIndex < 0 || beforeIndex < 0 || currentIndex < beforeIndex) return;
    const assignments = inputLinks(node, beforeIndex);
    const [added] = node.inputs.splice(currentIndex, 1);
    node.inputs.splice(beforeIndex, 0, added);
    repairTargetSlots(node, assignments);
}

export function syncImageInputs(node, requestedCount, beforeInputName = null) {
    const count = normalizeImageCount(requestedCount);
    const existing = (node.inputs ?? [])
        .map((input, index) => ({ input, index, number: imageNumber(input) }))
        .filter((entry) => entry.number !== null);

    for (const entry of existing) {
        entry.input.label = `Image ${entry.number}`;
    }

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
        if (!names.has(name)) {
            addInputBefore(
                node,
                name,
                "IMAGE",
                { label: `Image ${index}` },
                beforeInputName,
            );
        }
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
    const beforeInputName = ["LlamaCppGenerate", "LlamaCppRequestBudget"].includes(
        node.constructor?.comfyClass,
    )
        ? "structured_output"
        : null;

    syncImageInputs(node, widget.value ?? 2, beforeInputName);

    if (widget.__llamacppDynamicImages) return;
    widget.__llamacppDynamicImages = true;
    const originalCallback = widget.callback;
    widget.callback = function (value, ...args) {
        syncImageInputs(node, value, beforeInputName);
        app.graph?.setDirtyCanvas?.(true, true);
        return originalCallback?.call(this, value, ...args);
    };
}
