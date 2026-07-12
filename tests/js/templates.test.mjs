import test from "node:test";
import assert from "node:assert/strict";

import {
    applyTemplateValues,
    normalizeTemplates,
    setupTemplateWidget,
} from "../../web/template_utils.js";

const TEMPLATE_VALUES = {
    Empty: { system_prompt: "", prompt: "" },
    First: { system_prompt: "first system", prompt: "first prompt" },
    Second: { system_prompt: "second system", prompt: "second prompt" },
};

function deferred() {
    let resolve;
    let reject;
    const promise = new Promise((resolvePromise, rejectPromise) => {
        resolve = resolvePromise;
        reject = rejectPromise;
    });
    return { promise, reject, resolve };
}

function fakeNode({ prompt = "", systemPrompt = "", template = "Empty" } = {}) {
    const calls = [];
    const node = {
        constructor: { comfyClass: "LlamaCppAdvPPPrompt" },
        graph: {
            afterChange(changedNode) {
                calls.push(["after", changedNode]);
            },
            beforeChange(changedNode) {
                calls.push(["before", changedNode]);
            },
            setDirtyCanvas(foreground, background) {
                calls.push(["dirty", foreground, background]);
            },
        },
        widgets: [
            { name: "template", value: template },
            { name: "prompt", value: prompt },
            { name: "system_prompt", value: systemPrompt },
            { name: "seed", value: 7 },
        ],
        addWidget(type, name, value, callback, options = {}) {
            const widget = { callback, name, options, type, value };
            this.widgets.push(widget);
            return widget;
        },
    };
    return { calls, node };
}

function promptValues(node) {
    return {
        prompt: node.widgets.find((widget) => widget.name === "prompt").value,
        system_prompt: node.widgets.find((widget) => widget.name === "system_prompt").value,
    };
}

test("strict normalization adds Empty first and preserves valid template order", () => {
    const normalized = normalizeTemplates({
        First: { prompt: "prompt" },
        Second: { system_prompt: "system" },
    });

    assert.deepEqual(Object.keys(normalized), ["Empty", "First", "Second"]);
    assert.deepEqual(normalized, {
        Empty: { system_prompt: "", prompt: "" },
        First: { system_prompt: "", prompt: "prompt" },
        Second: { system_prompt: "system", prompt: "" },
    });
});

test("strict normalization rejects malformed roots, names, entries, and fields", () => {
    for (const value of [null, [], "templates"]) {
        assert.throws(() => normalizeTemplates(value), /must contain an object/);
    }
    assert.throws(() => normalizeTemplates({ "": {} }), /names must be non-empty/);
    assert.throws(() => normalizeTemplates({ Invalid: [] }), /must be an object/);
    assert.throws(
        () => normalizeTemplates({ Invalid: { prompt: 1, system_prompt: "system" } }),
        /fields must be strings/,
    );
    assert.throws(
        () => normalizeTemplates({ Invalid: { prompt: "prompt", system_prompt: null } }),
        /fields must be strings/,
    );
});

test("default application fills exact-empty fields independently without clobbering text", () => {
    const cases = [
        ["", "", { prompt: "first prompt", system_prompt: "first system" }],
        ["draft", "", { prompt: "draft", system_prompt: "first system" }],
        ["", "custom", { prompt: "first prompt", system_prompt: "custom" }],
        ["draft", "custom", { prompt: "draft", system_prompt: "custom" }],
        ["   ", "\n", { prompt: "   ", system_prompt: "\n" }],
    ];

    for (const [prompt, systemPrompt, expected] of cases) {
        const { node } = fakeNode({ prompt, systemPrompt });
        applyTemplateValues(node.widgets, TEMPLATE_VALUES.First);
        assert.deepEqual(promptValues(node), expected);
        assert.equal(node.widgets.find((widget) => widget.name === "seed").value, 7);
    }
});

test("Empty, missing, and malformed templates never erase prompt fields", () => {
    for (const template of [TEMPLATE_VALUES.Empty, undefined, [], { prompt: 1 }]) {
        const { node } = fakeNode({ prompt: "draft", systemPrompt: "custom" });
        assert.equal(applyTemplateValues(node.widgets, template), false);
        assert.deepEqual(promptValues(node), { prompt: "draft", system_prompt: "custom" });
    }
});

test("explicit replacement applies exact template values including empty reset values", () => {
    const { node } = fakeNode({ prompt: "draft", systemPrompt: "custom" });

    assert.equal(applyTemplateValues(node.widgets, TEMPLATE_VALUES.First, { replace: true }), true);
    assert.deepEqual(promptValues(node), {
        prompt: "first prompt",
        system_prompt: "first system",
    });
    assert.equal(applyTemplateValues(node.widgets, TEMPLATE_VALUES.Empty, { replace: true }), true);
    assert.deepEqual(promptValues(node), { prompt: "", system_prompt: "" });
});

test("latest template selection wins when template loading resolves out of order", async () => {
    const pending = deferred();
    const { node } = fakeNode();
    setupTemplateWidget(node, { loadTemplates: () => pending.promise });
    const template = node.widgets.find((widget) => widget.name === "template");

    template.value = "First";
    const first = template.callback("First");
    template.value = "Second";
    const second = template.callback("Second");
    pending.resolve(TEMPLATE_VALUES);
    await Promise.all([first, second]);

    assert.deepEqual(promptValues(node), {
        prompt: "second prompt",
        system_prompt: "second system",
    });
});

test("a draft typed while templates load remains authoritative", async () => {
    const pending = deferred();
    const { node } = fakeNode({ template: "First" });
    setupTemplateWidget(node, { loadTemplates: () => pending.promise });
    const template = node.widgets.find((widget) => widget.name === "template");

    const selection = template.callback("First");
    node.widgets.find((widget) => widget.name === "prompt").value = "typed draft";
    pending.resolve(TEMPLATE_VALUES);
    await selection;

    assert.deepEqual(promptValues(node), {
        prompt: "typed draft",
        system_prompt: "first system",
    });
});

test("setup is idempotent and appends one nonserialized Replace/Reset action", () => {
    const { node } = fakeNode();
    const options = { loadTemplates: async () => TEMPLATE_VALUES };

    assert.equal(setupTemplateWidget(node, options), true);
    const callback = node.widgets.find((widget) => widget.name === "template").callback;
    assert.equal(setupTemplateWidget(node, options), false);
    assert.equal(node.widgets.find((widget) => widget.name === "template").callback, callback);

    const actions = node.widgets.filter((widget) => widget.__llamacppTemplateAction);
    assert.equal(actions.length, 1);
    assert.equal(actions[0].serialize, false);
    assert.equal(actions[0].options.serialize, false);
    assert.equal(node.widgets.at(-1), actions[0]);
});

test("Replace/Reset records an undo transaction around the mutation", async () => {
    const { calls, node } = fakeNode({
        prompt: "draft",
        systemPrompt: "custom",
        template: "First",
    });
    setupTemplateWidget(node, { loadTemplates: async () => TEMPLATE_VALUES });
    const action = node.widgets.find((widget) => widget.__llamacppTemplateAction);

    assert.equal(await action.callback(), true);
    assert.deepEqual(promptValues(node), {
        prompt: "first prompt",
        system_prompt: "first system",
    });
    assert.deepEqual(
        calls.map((call) => call[0]),
        ["before", "after", "dirty"],
    );
    assert.equal(calls[0][1], node);
    assert.equal(calls[1][1], node);
});

test("selection errors are non-destructive and reported through the injected logger", async () => {
    const pending = deferred();
    const warnings = [];
    const { node } = fakeNode({ prompt: "draft", systemPrompt: "custom", template: "First" });
    setupTemplateWidget(node, {
        loadTemplates: () => pending.promise,
        logger: { warn: (...args) => warnings.push(args) },
    });

    const selection = node.widgets.find((widget) => widget.name === "template").callback("First");
    pending.reject(new Error("unavailable"));
    await selection;

    assert.deepEqual(promptValues(node), { prompt: "draft", system_prompt: "custom" });
    assert.equal(warnings.length, 1);
});
