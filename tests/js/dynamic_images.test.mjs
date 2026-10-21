import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

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
            const input = { name, type, link: null, ...options };
            this.inputs.push(input);
            return input;
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

/** Model the frontend's index-derived input.link and authoritative link store. */
function storeBackedNode(serialized = null) {
    const node = fakeNode(0);
    node.id = 3;
    node.constructor = { comfyClass: "LlamaCppGenerate" };
    const links = new Map();
    node.graph = { _links: links };
    node.getInputLink = (slot) => [...links.values()].find(
        (link) => link.target_slot === slot,
    ) ?? null;
    function inputSlot({ link: _serializedLink, ...data }) {
        const input = { ...data };
        Object.defineProperty(input, "link", {
            enumerable: true,
            get: () => node.getInputLink(node.inputs.indexOf(input))?.id ?? null,
        });
        return input;
    }
    function connect(name, id) {
        let targetSlot = node.inputs.findIndex((input) => input.name === name);
        assert.notEqual(targetSlot, -1);
        const link = {
            id,
            get target_slot() { return targetSlot; },
            set target_slot(value) { this.updateEndpoints({ targetSlot: value }); },
            updateEndpoints(patch) {
                const occupying = node.getInputLink(patch.targetSlot);
                assert.ok(!occupying || occupying === this, "link destination must be vacant");
                targetSlot = patch.targetSlot;
            },
        };
        links.set(id, link);
    }
    node.inputs = (serialized?.inputs ?? [
        { name: "connection", type: "LLAMACPP_CONNECTION" },
        { name: "profile", type: "LLAMACPP_PROFILE" },
        { name: "structured_output", type: "STRUCTURED_OUTPUT" },
        { name: "token_ban", type: "LOGIT_BIAS" },
        { name: "messages", type: "LLAMACPP_MESSAGES" },
        { name: "server_url", type: "STRING", widget: { name: "server_url" } },
    ]).map(inputSlot);
    node.addInput = (name, type, options = {}) => {
        const input = inputSlot({ name, type, ...options });
        node.inputs.push(input);
        return input;
    };
    node.removeInput = (index) => {
        const removed = node.getInputLink(index);
        if (removed) links.delete(removed.id);
        const moved = [...links.values()]
            .filter((link) => link.target_slot > index)
            .sort((left, right) => left.target_slot - right.target_slot);
        for (const link of moved) link.updateEndpoints({ targetSlot: link.target_slot - 1 });
        node.inputs.splice(index, 1);
    };
    node.serialize = () => ({
        inputs: node.inputs.map((input) => ({ ...input })),
        links: [...links.values()].map((link) => ({ id: link.id, target_slot: link.target_slot })),
    });
    const initialLinks = serialized?.links ?? node.inputs.map((_, index) => ({
        id: index + 1, target_slot: index,
    }));
    for (const link of initialLinks) connect(node.inputs[link.target_slot].name, link.id);
    return { node, connect };
}

test("image count preserves zero and clamps invalid values", () => {
    assert.equal(normalizeImageCount(0), 0);
    assert.equal(normalizeImageCount(null), 2);
    assert.equal(normalizeImageCount(99), 10);
    assert.equal(normalizeImageCount("bad"), 2);
});

for (const comfyClass of ["LlamaCppGenerate", "LlamaCppRequestBudget"]) {
    test(`${comfyClass} restores images before trailing typed sockets and repairs links`, () => {
        const structuredLink = { target_slot: 1 };
        const tokenLink = { target_slot: 2 };
        const node = fakeNode(0);
        node.constructor = { comfyClass };
        node.inputs.push(
            { name: "structured_output", type: "STRUCTURED_OUTPUT", link: 71 },
            { name: "token_ban", type: "LOGIT_BIAS", link: 72 },
        );
        node.graph = { _links: new Map([[71, structuredLink], [72, tokenLink]]) };

        setupDynamicImageInputs(node, { graph: {} });
        node.widgets[0].callback(2);

        assert.deepEqual(node.inputs.map((input) => input.name), [
            "trigger",
            "image_1",
            "image_2",
            "structured_output",
            "token_ban",
        ]);
        assert.equal(structuredLink.target_slot, 3);
        assert.equal(tokenLink.target_slot, 4);
    });
}

test("actual image extension targets Request Budget and leaves fixed-media nodes alone", async () => {
    const sourceURL = new URL("../../web/adv_prompt.js", import.meta.url);
    let extension;
    globalThis.__llamacppDynamicHarnessApp = {
        graph: {}, registerExtension: (value) => { extension = value; },
    };
    const source = (await readFile(sourceURL, "utf8"))
        .replace('import { app } from "../../scripts/app.js";',
            "const app = globalThis.__llamacppDynamicHarnessApp;")
        .replace('"./dynamic_images.js"', JSON.stringify(new URL("dynamic_images.js", sourceURL).href));
    try {
        await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
    } finally {
        delete globalThis.__llamacppDynamicHarnessApp;
    }
    const budget = fakeNode();
    budget.constructor = { comfyClass: "LlamaCppRequestBudget" };
    budget.widgets[0].value = 0;
    extension.nodeCreated(budget);
    assert.deepEqual(budget.inputs.map((input) => input.name), ["trigger"]);
    budget.widgets[0].value = 1;
    extension.loadedGraphNode(budget);
    assert.deepEqual(budget.inputs.map((input) => input.name), ["trigger", "image_1"]);
    for (const comfyClass of ["LlamaCppCaptions", "LlamaCppTranscribe"]) {
        const fixed = fakeNode();
        fixed.constructor = { comfyClass };
        const before = [...fixed.inputs];
        extension.nodeCreated(fixed);
        assert.deepEqual(fixed.inputs, before);
        assert.equal(fixed.widgets[0].callback, null);
    }
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

test("index-derived links stay on their named inputs across image changes and reload", () => {
    let { node, connect } = storeBackedNode();
    const expected = new Map(node.inputs.map((input) => [input.name, input.link]));
    function assertLinks() {
        for (const [name, id] of expected) {
            const index = node.inputs.findIndex((input) => input.name === name);
            assert.notEqual(index, -1, name);
            assert.equal(node.inputs[index].link, id, name);
            assert.equal(node.graph._links.get(id).target_slot, index, name);
        }
        for (const input of node.inputs) {
            assert.equal(input.link, expected.get(input.name) ?? null, input.name);
        }
    }
    for (const count of [0, 1, 10, 1, 0, 10]) {
        syncImageInputs(node, count, "structured_output");
        if (!count) expected.delete("image_1");
        if (count && !expected.has("image_1")) {
            connect("image_1", 99);
            expected.set("image_1", 99);
        }
        assertLinks();
        const saved = JSON.parse(JSON.stringify(node.serialize()));
        ({ node, connect } = storeBackedNode(saved));
        assertLinks();
    }
});
