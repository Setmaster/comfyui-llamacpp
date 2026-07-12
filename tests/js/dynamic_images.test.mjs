import test from "node:test";
import assert from "node:assert/strict";

import {
    normalizeImageCount,
    setupDynamicImageInputs,
    syncImageInputs,
} from "../../web/dynamic_images.js";

function fakeNode(count = 10) {
    const node = {
        inputs: [
            { name: "trigger", type: "*" },
            ...Array.from({ length: count }, (_, index) => ({
                name: `image_${index + 1}`,
                type: "IMAGE",
            })),
        ],
        widgets: [{ name: "image_amount", value: 2, callback: null }],
        size: [320, 100],
        removeInput(index) {
            this.inputs.splice(index, 1);
        },
        addInput(name, type, options = {}) {
            this.inputs.push({ name, type, ...options });
        },
        computeSize() {
            return [300, 150];
        },
        setSize(size) {
            this.size = size;
        },
    };
    return node;
}

test("image count preserves zero and clamps invalid values", () => {
    assert.equal(normalizeImageCount(0), 0);
    assert.equal(normalizeImageCount(null), 2);
    assert.equal(normalizeImageCount(99), 10);
    assert.equal(normalizeImageCount("bad"), 2);
});

test("sync removes only image sockets and restores named sockets", () => {
    const node = fakeNode();
    assert.equal(syncImageInputs(node, 0), 0);
    assert.deepEqual(node.inputs.map((input) => input.name), ["trigger"]);
    syncImageInputs(node, 3);
    assert.deepEqual(node.inputs.map((input) => input.name), [
        "trigger",
        "image_1",
        "image_2",
        "image_3",
    ]);
    assert.deepEqual(
        node.inputs.filter((input) => input.name.startsWith("image_")).map((input) => input.label),
        ["Image 1", "Image 2", "Image 3"],
    );
});

test("setup is idempotent and widget callback updates sockets", () => {
    const node = fakeNode();
    let dirty = 0;
    const app = { graph: { setDirtyCanvas: () => { dirty += 1; } } };
    setupDynamicImageInputs(node, app);
    const callback = node.widgets[0].callback;
    setupDynamicImageInputs(node, app);
    assert.equal(node.widgets[0].callback, callback);
    callback(0);
    assert.equal(node.inputs.some((input) => input.name.startsWith("image_")), false);
    assert.equal(dirty, 1);
});

test("loaded graph setup restores saved image counts 0, 1, and 10", () => {
    for (const count of [0, 1, 10]) {
        const node = fakeNode();
        node.widgets[0].value = count;

        setupDynamicImageInputs(node, { graph: {} });

        assert.deepEqual(
            node.inputs.filter((input) => input.name.startsWith("image_")).map((input) => input.name),
            Array.from({ length: count }, (_, index) => `image_${index + 1}`),
        );
        assert.deepEqual(
            node.inputs.filter((input) => input.name.startsWith("image_")).map((input) => input.label),
            Array.from({ length: count }, (_, index) => `Image ${index + 1}`),
        );
    }
});
