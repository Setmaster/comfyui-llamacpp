import test from "node:test";
import assert from "node:assert/strict";

import {
    ADVANCED_NODE_IDS,
    fitNodeToVisibleWidgets,
    setupAdvancedWidgetCompatibility,
} from "../../web/advanced_widgets.js";

test("compatibility setup targets every node with advanced metadata", () => {
    assert.deepEqual([...ADVANCED_NODE_IDS], [
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
        "LlamaCppGenerate",
    ]);
});

function fakeNode() {
    const normal = { name: "prompt", options: {} };
    const advanced = { name: "top_p", options: { advanced: true } };
    const seedControl = { name: "control_after_generate", options: {} };
    const seed = {
        name: "seed",
        options: { advanced: true },
        linkedWidgets: [seedControl],
    };
    let connectionCalls = 0;
    const node = {
        widgets: [normal, advanced, seed, seedControl],
        inputs: [
            { name: "top_p", widget: { name: "top_p" }, link: null },
            { name: "seed", widget: { name: "seed" }, link: null },
        ],
        size: [500, 900],
        showAdvanced: false,
        getLayoutWidgets() {
            return this.widgets.filter((widget) => !widget.hidden);
        },
        computeSize() {
            return [320, 40 + this.getLayoutWidgets().length * 20];
        },
        setSize(size) {
            this.size = size;
        },
        onConnectionsChange() {
            connectionCalls += 1;
            assert.equal(this, node);
            return "original result";
        },
    };
    return {
        node,
        normal,
        advanced,
        seed,
        seedControl,
        connectionCalls: () => connectionCalls,
    };
}

test("classic widgets collapse, including seed companion controls", () => {
    const { node, normal, advanced, seed, seedControl } = fakeNode();

    setupAdvancedWidgetCompatibility(node);

    assert.equal(normal.advanced, false);
    assert.equal(advanced.advanced, true);
    assert.equal(seed.advanced, true);
    assert.equal(seedControl.advanced, true);
    assert.equal(seedControl.options.advanced, true);
    assert.deepEqual(node.getLayoutWidgets(), [normal]);

    node.showAdvanced = true;
    assert.deepEqual(node.getLayoutWidgets(), [normal, advanced, seed, seedControl]);
});

test("linked advanced widgets and companions stay visible after connection changes", () => {
    const { node, normal, advanced, seed, seedControl, connectionCalls } = fakeNode();
    setupAdvancedWidgetCompatibility(node);
    const connectionHandler = node.onConnectionsChange;
    setupAdvancedWidgetCompatibility(node);
    assert.equal(node.onConnectionsChange, connectionHandler);

    node.size = [250, 60];
    node.inputs[1].link = 17;
    assert.equal(node.onConnectionsChange("input", 1, true), "original result");
    assert.equal(connectionCalls(), 1);
    assert.equal(seed.advanced, false);
    assert.equal(seedControl.advanced, false);
    assert.equal(seedControl.options.advanced, false);
    assert.deepEqual(node.getLayoutWidgets(), [normal, seed, seedControl]);
    assert.deepEqual(node.size, [320, 100]);

    node.inputs[1].link = null;
    node.onConnectionsChange("input", 1, false);
    assert.equal(seed.advanced, true);
    assert.equal(seedControl.advanced, true);
    assert.deepEqual(node.getLayoutWidgets(), [normal]);
    assert.deepEqual(node.size, [320, 100]);

    node.inputs[0].link = 21;
    node.onConnectionsChange("input", 0, true);
    assert.equal(advanced.advanced, false);
    assert.deepEqual(node.getLayoutWidgets(), [normal, advanced]);
});

test("new nodes fit visible widgets while loaded nodes keep their serialized size", () => {
    const loaded = fakeNode().node;
    setupAdvancedWidgetCompatibility(loaded);
    assert.deepEqual(loaded.size, [500, 900]);

    const created = fakeNode().node;
    setupAdvancedWidgetCompatibility(created, { fit: true });
    assert.deepEqual(created.size, [500, 60]);

    created.size = [250, 200];
    fitNodeToVisibleWidgets(created);
    assert.deepEqual(created.size, [320, 60]);
});
