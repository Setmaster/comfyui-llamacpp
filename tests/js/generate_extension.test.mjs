import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { EXPERT_SAMPLERS } from "../../web/generate_controls.js";

const sourceURL = new URL("../../web/generate.js", import.meta.url);
const extensionSource = await readFile(sourceURL, "utf8");
let harnessSerial = 0;

function deferred() {
    let resolve;
    const promise = new Promise((fulfill) => { resolve = fulfill; });
    return { promise, resolve };
}

function snapshot(overrides = {}) {
    return {
        schema_version: 1,
        kind: "snapshot",
        execution_id: "00000000-0000-4000-8000-00000000000a",
        workflow_id: "workflow-a",
        prompt_id: "prompt-a",
        node_id: "7",
        display_node_id: "7",
        real_node_id: "7",
        parent_node_id: null,
        list_index: null,
        sequence: 1,
        started_at_ms: 1000,
        elapsed_ms: 100,
        phase: "generating",
        terminal: false,
        response: { text: "draft", bytes: 5, total_bytes: 5, truncated: false },
        thinking: { text: "", bytes: 0, total_bytes: 0, truncated: false },
        cancel: { scope: "generation", enabled: true },
        ...overrides,
    };
}

/** Import the real extension, replacing only Comfy's host-module boundary. */
async function createHarness({
    savedModel = "(use running model)", discovery = null, activeSnapshots = [],
    comfyClass = "LlamaCppGenerate", withModel = true, refresh = false,
    prepareNode = null,
} = {}) {
    const listeners = new Map();
    const cancellation = deferred();
    const requests = [];
    const graph = {
        id: "workflow-a",
        getNodeById: (id) => String(id) === "7" ? node : null,
        setDirtyCanvas() {},
    };
    const api = {
        clientId: "client-a",
        addEventListener(name, callback) { listeners.set(name, callback); },
        fetchApi(url, options) {
            requests.push({ url, options });
            if (url.includes("/cancel")) return cancellation.promise;
            return Promise.resolve({
                ok: true,
                json: async () => url.includes("/discovery")
                    ? discovery : { executions: activeSnapshots },
            });
        },
    };
    let extension;
    const app = {
        rootGraph: graph,
        graph,
        registerExtension(value) { extension = value; },
    };
    const ComfyWidgets = {
        STRING(target, name) {
            const widget = { name, value: "", options: {} };
            target.widgets.push(widget);
            return { widget };
        },
    };
    const node = {
        constructor: { comfyClass },
        graph,
        widgets: withModel ? [{ name: "model", value: savedModel, options: {} }] : [],
        size: [420, 100],
        computeSize() { return this.size; },
        setSize() {},
        addWidget(type, name, value, callback, options = {}) {
            const widget = { type, name, value, callback, options };
            this.widgets.push(widget);
            return widget;
        },
    };
    prepareNode?.(node);
    const key = `__llamacppExtensionHarness${++harnessSerial}`;
    globalThis[key] = { api, app, ComfyWidgets };
    let source = extensionSource;
    for (const [name, file] of [["api", "api"], ["app", "app"], ["ComfyWidgets", "widgets"]]) {
        source = source.replace(
            `import { ${name} } from "../../scripts/${file}.js";`,
            `const { ${name} } = globalThis[${JSON.stringify(key)}];`,
        );
    }
    source = source.replaceAll(
        /"\.\/(generate_controls|generate_live_state)\.js"/g,
        (_, name) => JSON.stringify(new URL(`${name}.js`, sourceURL).href),
    );
    let module;
    try {
        module = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
    } finally {
        delete globalThis[key];
    }
    extension.setup();
    module.setupGenerateNode(node, { refresh });
    await new Promise((resolve) => setImmediate(resolve));
    return {
        api, graph, node, requests, cancellation,
        state: node.__llamacppGenerateState,
        setup: () => module.setupGenerateNode(node, { refresh }),
        loaded: () => extension.loadedGraphNode(node),
        emit: (value) => listeners.get("llamacpp.generation")({ detail: value }),
        execute: (detail) => listeners.get("executing")?.({ detail }),
    };
}

function samplingFixture(node) {
    node.inputs = [];
    node.samplingCalls = [];
    node.addWidget("combo", "sampling_mode", "default", (value) => {
        node.samplingCalls.push(["mode", value]);
        return "mode changed";
    });
    for (const [index, name] of EXPERT_SAMPLERS.entries()) {
        node.addWidget("number", name, index + 0.25, null).computeSize = () => [200, 20];
    }
    node.onConfigure = () => {
        node.samplingCalls.push(["configure"]);
        return "configured";
    };
    node.onConnectionsChange = () => {
        node.samplingCalls.push(["connection"]);
        return "connected";
    };
}

function assertSamplingVisibility(node, visibleNames) {
    for (const name of EXPERT_SAMPLERS) {
        const current = node.widgets.find((item) => item.name === name);
        const hidden = !visibleNames.includes(name);
        assert.equal(current.hidden, hidden, name);
        assert.equal(current.options.hidden, hidden, name);
        assert.deepEqual(current.computeSize(), hidden ? [0, -4] : [200, 20], name);
    }
}

for (const comfyClass of ["LlamaCppGenerate", "LlamaCppCaptions", "LlamaCppRequestBudget"]) {
    test(`${comfyClass} reconciles sampler visibility on toggle, configure and graph reload`, async () => {
        const harness = await createHarness({ comfyClass, prepareNode: samplingFixture });
        const { node } = harness;
        const mode = node.widgets.find((item) => item.name === "sampling_mode");
        const samplerValues = () => EXPERT_SAMPLERS.map(
            (name) => node.widgets.find((item) => item.name === name).value,
        );
        const savedValues = samplerValues();
        assertSamplingVisibility(node, []);
        for (const value of ["custom", "default"]) {
            mode.value = value;
            assert.equal(mode.callback(value), "mode changed");
            assertSamplingVisibility(node, value === "custom" ? EXPERT_SAMPLERS : []);
        }
        harness.setup();
        // Clone/paste restores values before onConfigure, without loadedGraphNode.
        for (const value of ["custom", "default"]) {
            mode.value = value;
            assert.equal(node.onConfigure({}), "configured");
            assertSamplingVisibility(node, value === "custom" ? EXPERT_SAMPLERS : []);
        }
        // Saved-graph loading also reconciles values without invoking callbacks.
        for (const value of ["custom", "default"]) {
            mode.value = value;
            harness.loaded();
            assertSamplingVisibility(node, value === "custom" ? EXPERT_SAMPLERS : []);
        }
        assert.deepEqual(samplerValues(), savedValues);
        assert.deepEqual(node.samplingCalls, [
            ["mode", "custom"], ["mode", "default"], ["configure"], ["configure"],
        ]);
        if (comfyClass !== "LlamaCppGenerate") {
            assert.equal(harness.state.modelStatus, null);
            assert.equal(harness.state.refreshButton, null);
            assert.equal(harness.state.discoveryTimer, null);
            assert.equal(harness.requests.some(({ url }) => url.includes("/discovery")), false);
        }
        node.onRemoved();
    });

    test(`${comfyClass} keeps linked sampler inputs visible in default mode`, async () => {
        const { node } = await createHarness({ comfyClass, prepareNode: samplingFixture });
        node.inputs = [{ name: "temperature", link: 10 }];
        assert.equal(node.onConnectionsChange(), "connected");
        assertSamplingVisibility(node, ["temperature"]);
        node.inputs.push({ name: "sampling_mode", link: 11 });
        node.onConnectionsChange();
        assertSamplingVisibility(node, EXPERT_SAMPLERS);
        node.inputs = [];
        node.onConnectionsChange();
        assertSamplingVisibility(node, []);
        node.onRemoved();
    });
}

test("Transcribe never installs sampler reconciliation or changes unrelated controls", async () => {
    const { node, loaded, state } = await createHarness({
        comfyClass: "LlamaCppTranscribe", prepareNode: samplingFixture,
    });
    const mode = node.widgets.find((item) => item.name === "sampling_mode");
    assert.equal(mode.__llamacppSamplingMode, undefined);
    assert.equal(node.__llamacppGeneratePostConfigure, undefined);
    loaded();
    node.onConfigure({});
    mode.value = "custom";
    mode.callback("custom");
    for (const name of EXPERT_SAMPLERS) {
        const current = node.widgets.find((item) => item.name === name);
        assert.equal(current.hidden, undefined);
        assert.equal(current.options.hidden, undefined);
        assert.deepEqual(current.computeSize(), [200, 20]);
    }
    assert.equal(state.discoveryTimer, null);
    node.onRemoved();
});

test("real extension shows preflight failure and ignores unrelated workflow events", async () => {
    const harness = await createHarness();
    harness.emit(snapshot({ phase: "starting", response: { text: "" } }));
    harness.emit(snapshot({
        sequence: 2,
        phase: "failed",
        terminal: true,
        response: { text: "" },
        cancel: { scope: "none", enabled: false },
        error: { category: "runtime_unavailable", message: "No managed runtime is running" },
    }));
    const expected = harness.state.liveStatus.value;
    assert.match(expected, /^failed.*No managed runtime is running/);
    assert.equal(harness.state.cancel.disabled, true);
    harness.emit(snapshot({ workflow_id: "workflow-b", started_at_ms: 2000 }));
    harness.execute({ node: "7", prompt_id: "unrelated-prompt" });
    assert.equal(harness.state.liveStatus.value, expected);
});

for (const [comfyClass, statusName, responseName, thinkingName] of [
    ["LlamaCppTranscribe", "Transcription Status", "Raw ASR Response", null],
    ["LlamaCppCaptions", "Caption Batch Status", "Current Caption Response", "Current Caption Thinking"],
    ["LlamaCppRequestBudget", "Request Budget Status", null, null],
]) {
    test(`${comfyClass} restores live controls without Generate widgets or discovery`, async () => {
        const restored = snapshot({ cancel: { scope: "prompt", enabled: true } });
        const harness = await createHarness({
            comfyClass, withModel: false, refresh: true, activeSnapshots: [restored],
        });
        assert.equal(harness.state.liveStatus.name, statusName);
        assert.match(harness.state.liveStatus.value, /^generating/);
        assert.equal(harness.state.response?.name ?? null, responseName);
        assert.equal(harness.state.thinking?.name ?? null, thinkingName);
        if (responseName) assert.equal(harness.state.response.value, "draft");
        assert.equal(harness.state.modelStatus, null);
        assert.equal(harness.state.refreshButton, null);
        assert.equal(harness.state.discoveryTimer, null);
        const widgets = [...harness.node.widgets];
        harness.setup();
        assert.deepEqual(harness.node.widgets, widgets);
        await harness.state.cancel.callback();
        const interrupt = harness.requests.find(({ url }) => url === "/interrupt");
        assert.deepEqual(JSON.parse(interrupt.options.body), { prompt_id: "prompt-a" });
        assert.equal(harness.requests.some(({ url }) => url.includes("/discovery")), false);
        for (const widget of widgets) {
            assert.equal(widget.options.serialize, false);
            assert.equal(widget.serializeValue(), undefined);
        }
        harness.emit(snapshot({
            sequence: 2, phase: "complete", terminal: true,
            cancel: { scope: "none", enabled: false },
        }));
        assert.match(harness.state.liveStatus.value, /^complete/);
        assert.equal(harness.state.cancel.disabled, true);
        harness.emit(snapshot({
            execution_id: "00000000-0000-4000-8000-00000000000b",
            prompt_id: "prompt-b", started_at_ms: 2000, phase: "starting",
            response: { text: "" }, thinking: { text: "" },
        }));
        if (responseName) assert.equal(harness.state.response.value, "");
        if (thinkingName) assert.equal(harness.state.thinking.value, "");
    });
}

test("pure result and message utilities receive no execution UI", async () => {
    for (const comfyClass of ["LlamaCppResult", "LlamaCppMessage", "LlamaCppMessages"]) {
        const harness = await createHarness({ comfyClass, withModel: false });
        assert.equal(harness.state, undefined);
        assert.deepEqual(harness.node.widgets, []);
    }
});

for (const responseOK of [true, false]) {
    for (const transition of ["terminal", "new_execution", "releasing", "workflow", "client", "removed"]) {
        test(`delayed Stop ${responseOK ? "success" : "failure"} preserves ${transition}`, async () => {
            const harness = await createHarness();
            harness.emit(snapshot());
            const stopping = harness.state.cancel.callback();
            if (transition === "terminal") {
                harness.emit(snapshot({
                    sequence: 2, phase: "cancelled", terminal: true,
                    cancel: { scope: "none", enabled: false },
                }));
            } else if (transition === "new_execution") {
                harness.emit(snapshot({
                    execution_id: "00000000-0000-4000-8000-00000000000b",
                    prompt_id: "prompt-b", started_at_ms: 2000, phase: "loading",
                }));
                assert.equal(harness.state.cancel.disabled, false);
            } else if (transition === "releasing") {
                harness.emit(snapshot({
                    sequence: 2, phase: "releasing", cancel: { scope: "none", enabled: false },
                }));
            } else if (transition === "workflow") {
                harness.graph.id = "workflow-b";
            } else if (transition === "client") {
                harness.api.clientId = "client-b";
            } else {
                harness.node.onRemoved();
            }
            const before = {
                status: harness.state.liveStatus.value,
                disabled: harness.state.cancel.disabled,
                label: harness.state.cancel.label,
            };
            harness.cancellation.resolve({
                ok: responseOK, status: responseOK ? 202 : 404,
                json: async () => responseOK
                    ? { upstream_confirmed: true }
                    : { error: { message: "not active" } },
            });
            await stopping;
            assert.deepEqual({
                status: harness.state.liveStatus.value,
                disabled: harness.state.cancel.disabled,
                label: harness.state.cancel.label,
            }, before);
            const request = harness.requests.find(({ url }) => url.includes("/cancel"));
            assert.match(request.url, /client_id=client-a/);
            assert.deepEqual(JSON.parse(request.options.body), {
                execution_id: snapshot().execution_id, prompt_id: "prompt-a", node_id: "7",
            });
        });
    }
}

test("current Stop failure stays visible and retryable", async () => {
    const harness = await createHarness();
    harness.emit(snapshot());
    const stopping = harness.state.cancel.callback();
    harness.cancellation.resolve({
        ok: false, status: 503, json: async () => ({ error: { message: "temporarily unavailable" } }),
    });
    const originalWarn = console.warn;
    console.warn = () => {};
    try {
        await stopping;
    } finally {
        console.warn = originalWarn;
    }
    assert.equal(harness.state.liveStatus.value, "Stop failed: temporarily unavailable");
    assert.equal(harness.state.cancel.disabled, false);
    assert.equal(harness.state.cancel.label, "Stop generation");
});

test("actual Refresh keeps the proven nested direct selection available", async () => {
    const harness = await createHarness({
        savedModel: "bundle/model.gguf",
        discovery: {
            schema_version: 1, mode: "direct", owned: true, saved_model_state: "available",
            models: [{
                model_id: "model.gguf", aliases: ["bundle/model.gguf"], residency: "loaded",
                context_length: { state: "known", value: 4096 },
                input_capabilities: { image: { state: "known", value: false } },
            }],
        },
    });
    await harness.state.refreshButton.callback();
    assert.equal(harness.state.model.value, "bundle/model.gguf");
    assert.match(harness.state.modelStatus.value, /bundle\/model\.gguf is available/);
    assert.match(harness.state.modelStatus.value, /context 4096/);
});

test("truncated live fields keep their notice through terminal updates and ignore stale events", async () => {
    const harness = await createHarness();
    const incoming = snapshot({
        sequence: 2,
        response: { text: "response tail", bytes: 13, total_bytes: 90000, truncated: true },
        thinking: { text: "thinking tail", bytes: 13, total_bytes: 80000, truncated: true },
    });
    const original = JSON.stringify(incoming);
    harness.emit(incoming);
    const notice = "[Preview truncated: showing the end only]\n\n";
    assert.equal(harness.state.response.value, `${notice}response tail`);
    assert.equal(harness.state.thinking.value, `${notice}thinking tail`);

    harness.emit(snapshot({ sequence: 1 }));
    assert.equal(harness.state.response.value, `${notice}response tail`);
    harness.emit({
        ...incoming, sequence: 3, phase: "complete", terminal: true,
        cancel: { scope: "none", enabled: false },
    });
    assert.match(harness.state.liveStatus.value, /^complete/);
    assert.equal(harness.state.response.value, `${notice}response tail`);
    assert.equal(harness.state.thinking.value, `${notice}thinking tail`);
    assert.equal(JSON.stringify(incoming), original);

    // The same widgets are bound into App Mode; their names and serialization
    // contract must remain stable when a notice is displayed.
    assert.equal(harness.state.response.name, "Live Response");
    assert.equal(harness.state.thinking.name, "Live Thinking");
    for (const widget of [harness.state.response, harness.state.thinking]) {
        assert.equal(widget.options.read_only, true);
        assert.equal(widget.options.serialize, false);
        assert.equal(widget.serialize, false);
        assert.equal(widget.serializeValue(), undefined);
    }

    harness.emit(snapshot({
        execution_id: "00000000-0000-4000-8000-00000000000b",
        prompt_id: "prompt-b", started_at_ms: 2000, phase: "starting",
        response: { text: "", truncated: false }, thinking: { text: "", truncated: false },
    }));
    assert.equal(harness.state.response.value, "");
    assert.equal(harness.state.thinking.value, "");
    harness.emit({ ...incoming, sequence: 4 });
    assert.equal(harness.state.response.value, "");
    assert.equal(harness.state.thinking.value, "");
});

test("restored truncated previews display a notice and full replacements clear it", async () => {
    const restored = snapshot({
        response: { text: "restored tail", truncated: true },
        thinking: { text: "whole thought", truncated: false },
    });
    const harness = await createHarness({ activeSnapshots: [restored] });
    assert.equal(
        harness.state.response.value,
        "[Preview truncated: showing the end only]\n\nrestored tail",
    );
    assert.equal(harness.state.thinking.value, "whole thought");
    harness.emit(snapshot({
        sequence: 2, phase: "failed", terminal: true,
        response: { text: "short final response", truncated: false },
        thinking: { text: "", truncated: false },
        cancel: { scope: "none", enabled: false },
    }));
    assert.equal(harness.state.response.value, "short final response");
    assert.equal(harness.state.thinking.value, "");
});
