import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
    migrateLegacyWorkflowData,
    restoreLegacyPromptOutput,
} from "../../web/workflow_compat.js";

const HISTORICAL_WORKFLOW = JSON.parse(
    readFileSync(
        new URL("../fixtures/workflows/v0_2_1_saved_workflow.json", import.meta.url),
        "utf8",
    ),
);

function historicalWorkflow() {
    return structuredClone(HISTORICAL_WORKFLOW);
}

test("exact 0.2.1 layouts gain the seed companion without shifting later values", () => {
    const workflow = historicalWorkflow();

    assert.deepEqual(migrateLegacyWorkflowData(workflow), {
        promptOutputs: 1,
        seedCompanions: 3,
    });
    const basic = workflow.nodes.find((node) => node.type === "LlamaCppBasicPrompt");
    const adv = workflow.nodes.find((node) => node.type === "LlamaCppAdvPrompt");
    const advpp = workflow.nodes.find((node) => node.type === "LlamaCppAdvPPPrompt");
    for (const [node, seedIndex] of [
        [basic, 11],
        [adv, 14],
        [advpp, 15],
    ]) {
        assert.equal(node.widgets_values[seedIndex + 1], "randomize");
    }
    assert.deepEqual(basic.widgets_values.slice(13, 15), [true, true]);
    assert.deepEqual(adv.widgets_values.slice(16, 18), [true, true]);
    assert.deepEqual(advpp.widgets_values.slice(17, 20), [false, true, false]);
});

test("historical Prompt Output keeps plaintext position and stages its display value", () => {
    const workflow = historicalWorkflow();
    const node = workflow.nodes.find((item) => item.type === "LlamaCppPromptOutput");

    migrateLegacyWorkflowData(workflow);

    assert.deepEqual(node.widgets_values, [true]);
    const widget = { value: "" };
    assert.equal(restoreLegacyPromptOutput({ properties: node.properties }, widget), true);
    assert.equal(widget.value, "Persisted historical output");
    assert.deepEqual(node.properties, { "Node name for S&R": "LlamaCppPromptOutput" });
    assert.equal(restoreLegacyPromptOutput({ properties: node.properties }, widget), false);
});

test("migration is idempotent", () => {
    const workflow = historicalWorkflow();

    migrateLegacyWorkflowData(workflow);
    const once = structuredClone(workflow);

    assert.deepEqual(migrateLegacyWorkflowData(workflow), {
        promptOutputs: 0,
        seedCompanions: 0,
    });
    assert.deepEqual(workflow, once);
});

test("current and ambiguous widget arrays are left unchanged", () => {
    const workflow = {
        nodes: [
            {
                type: "LlamaCppBasicPrompt",
                widgets_values: [
                    "prompt",
                    "model",
                    "url",
                    "system",
                    false,
                    777,
                    0.4,
                    0.8,
                    33,
                    0.1,
                    1.2,
                    123,
                    "fixed",
                    true,
                    true,
                ],
            },
            {
                type: "LlamaCppAdvPrompt",
                widgets_values: ["too", "short"],
            },
            {
                type: "LlamaCppPromptOutput",
                widgets_values: [false],
            },
            {
                type: "LlamaCppPromptOutput",
                widgets_values: [false, 42],
            },
        ],
    };
    const original = structuredClone(workflow);

    assert.deepEqual(migrateLegacyWorkflowData(workflow), {
        promptOutputs: 0,
        seedCompanions: 0,
    });
    assert.deepEqual(workflow, original);
});
