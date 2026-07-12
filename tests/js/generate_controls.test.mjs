import assert from "node:assert/strict";
import test from "node:test";

import {
    EXPERT_SAMPLERS,
    RUNNING_MODEL,
    applySavedModelHint,
    formatGenerationStatus,
    installPostConfigureReconciliation,
    isCustomSampling,
    markWidgetReadOnly,
    normalizeDiscovery,
    reconcileDiscoverySelection,
    shouldShowExpertSampling,
} from "../../web/generate_controls.js";

test("sampling mode recognizes only explicit custom mode", () => {
    assert.equal(isCustomSampling("Custom"), true);
    assert.equal(isCustomSampling("custom"), true);
    assert.equal(isCustomSampling("Default"), false);
    assert.equal(EXPERT_SAMPLERS.length, 7);
    assert.equal(shouldShowExpertSampling("Default", false), false);
    assert.equal(shouldShowExpertSampling("Custom", false), true);
    assert.equal(shouldShowExpertSampling("Default", true), true);
});

test("read-only widgets work in Nodes 2.0 and classic DOM widgets", () => {
    const element = { readOnly: false };
    const widget = { options: {}, inputEl: element };

    assert.equal(markWidgetReadOnly(widget), widget);
    assert.equal(widget.options.read_only, true);
    assert.equal(element.readOnly, true);
});

test("post-configure reconciliation observes clone and paste widget values", () => {
    const calls = [];
    const node = {
        mode: "Default",
        onConfigure(info) {
            calls.push(["original", this.mode, info.mode]);
            this.mode = info.mode;
            return "configured";
        },
    };
    assert.equal(
        installPostConfigureReconciliation(
            node,
            "__testPostConfigure",
            (configured, info) => calls.push(["reconcile", configured.mode, info.mode]),
        ),
        true,
    );
    assert.equal(node.onConfigure({ mode: "Custom" }), "configured");
    assert.deepEqual(calls, [
        ["original", "Default", "Custom"],
        ["reconcile", "Custom", "Custom"],
    ]);
    assert.equal(
        installPostConfigureReconciliation(node, "__testPostConfigure", () => {}),
        false,
    );
});

test("discovery keeps a raw missing saved model without claiming availability", () => {
    const result = normalizeDiscovery(
        {
            schema_version: 1,
            mode: "router",
            owned: true,
            saved_model_state: "missing",
            warnings: [],
            models: [{ model_id: "available.gguf" }],
        },
        "missing.gguf",
    );
    assert.deepEqual(result.choices, [RUNNING_MODEL, "available.gguf", "missing.gguf"]);
    assert.equal(result.state, "missing");
    assert.match(result.status, /missing\.gguf is missing/);
});

test("discovery exposes Known and Unknown selected facts without auto-confirming a projector", () => {
    const result = normalizeDiscovery(
        {
            schema_version: 1,
            mode: "router",
            owned: true,
            saved_model_state: "available",
            warnings: [],
            models: [
                {
                    model_id: "vision.gguf",
                    aliases: ["vision"],
                    residency: "unloaded",
                    context_length: { state: "unknown", value: null },
                    input_capabilities: {
                        image: { state: "known", value: true },
                    },
                    projector: {
                        projector_name: "mmproj-vision.gguf",
                        requires_confirmation: true,
                    },
                },
            ],
        },
        "vision",
    );

    assert.match(result.status, /residency unloaded/);
    assert.match(result.status, /context Unknown/);
    assert.match(result.status, /image Yes/);
    assert.match(result.status, /projector suggestion mmproj-vision\.gguf \(confirm\)/);
    assert.equal(result.choices.includes("mmproj-vision.gguf"), false);
});

test("invalid discovery stays unknown and still retains the saved value", () => {
    assert.deepEqual(normalizeDiscovery({}, "raw.gguf").choices, [RUNNING_MODEL, "raw.gguf"]);
    assert.equal(normalizeDiscovery({}, "raw.gguf").state, "unknown");
});

test("async discovery reconciliation never overwrites a newer model selection", () => {
    const discovery = normalizeDiscovery(
        {
            schema_version: 1,
            mode: "router",
            owned: true,
            saved_model_state: "available",
            warnings: [],
            models: [{ model_id: "requested.gguf" }, { model_id: "new.gguf" }],
        },
        "requested.gguf",
    );
    const reconciled = reconcileDiscoverySelection(
        discovery,
        "requested.gguf",
        "new.gguf",
    );

    assert.equal(reconciled.selected, "new.gguf");
    assert.equal(reconciled.choices.includes("new.gguf"), true);
    assert.match(reconciled.status, /Selection changed during Refresh/);
});

test("one automatic discovery snapshot derives saved availability per node", () => {
    const snapshot = {
        schema_version: 1,
        saved_model: null,
        saved_model_state: "unspecified",
        models: [{ model_id: "model-a", aliases: ["alias-a"] }],
    };

    assert.equal(applySavedModelHint(snapshot, "model-a").saved_model_state, "available");
    assert.equal(applySavedModelHint(snapshot, "alias-a").saved_model_state, "available");
    assert.equal(applySavedModelHint(snapshot, "missing").saved_model_state, "missing");
    assert.equal(snapshot.saved_model_state, "unspecified");
});

test("live status reports observed state without overclaiming release or progress", () => {
    assert.equal(formatGenerationStatus(null), "Idle");
    assert.equal(
        formatGenerationStatus({
            phase: "generating",
            elapsed_ms: 1250,
            prompt_progress: { percent: 50 },
            partial: false,
            release: null,
            error: null,
        }),
        "generating (1.3s, prompt 50.0%)",
    );
    assert.equal(
        formatGenerationStatus({
            phase: "complete",
            elapsed_ms: 2000,
            partial: false,
            stream_cleanup: { attempts: 1, confirmed: false, failures: 1 },
        }),
        "complete (2.0s, stream cleanup unconfirmed)",
    );
    assert.equal(
        formatGenerationStatus({
            phase: "complete",
            elapsed_ms: 2000,
            partial: false,
            stream_cleanup: { attempts: 2, confirmed: true, failures: 1 },
        }),
        "complete (2.0s)",
    );
});
