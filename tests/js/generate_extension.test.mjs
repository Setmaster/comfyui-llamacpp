import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

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
async function createHarness({ savedModel = "(use running model)", discovery = null } = {}) {
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
                json: async () => url.includes("/discovery") ? discovery : { executions: [] },
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
        constructor: { comfyClass: "LlamaCppGenerate" },
        graph,
        widgets: [{ name: "model", value: savedModel, options: {} }],
        size: [420, 100],
        computeSize() { return this.size; },
        setSize() {},
        addWidget(type, name, value, callback, options = {}) {
            const widget = { type, name, value, callback, options };
            this.widgets.push(widget);
            return widget;
        },
    };
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
    module.setupGenerateNode(node, { refresh: false });
    await new Promise((resolve) => setImmediate(resolve));
    return {
        api, graph, node, requests, cancellation,
        state: node.__llamacppGenerateState,
        emit: (value) => listeners.get("llamacpp.generation")({ detail: value }),
        execute: (detail) => listeners.get("executing")?.({ detail }),
    };
}

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
