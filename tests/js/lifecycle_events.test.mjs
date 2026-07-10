import test from "node:test";
import assert from "node:assert/strict";

import {
    classifyLifecycleEvent,
    showLifecycleNotice,
} from "../../web/lifecycle_events.js";

function fakeLogger() {
    const calls = { error: [], info: [], warn: [] };
    return {
        calls,
        error: (...args) => calls.error.push(args),
        info: (...args) => calls.info.push(args),
        warn: (...args) => calls.warn.push(args),
    };
}

test("failed releases use the supported extension toast API", () => {
    const messages = [];
    const logger = fakeLogger();
    const channel = showLifecycleNotice(
        { status: "failed", success: false, error: "inspect local diagnostics" },
        {
            appRef: { extensionManager: { toast: { add: (message) => messages.push(message) } } },
            logger,
        },
    );

    assert.equal(channel, "toast");
    assert.equal(messages.length, 1);
    assert.equal(messages[0].severity, "error");
    assert.equal(messages[0].summary, "llama.cpp release failed");
    assert.equal(messages[0].detail, "inspect local diagnostics");
    assert.equal(logger.calls.error.length, 0);
});

test("deferred release is informational even though success is false", () => {
    const notice = classifyLifecycleEvent({ status: "deferred", success: false });

    assert.equal(notice.severity, "info");
    assert.equal(notice.summary, "llama.cpp release queued");
    assert.match(notice.detail, /active generation/i);
});

test("coalesced and successful release events do not create notices", () => {
    assert.equal(classifyLifecycleEvent({ status: "coalesced", success: false }), null);
    assert.equal(classifyLifecycleEvent({ status: "complete", success: true }), null);
    assert.equal(classifyLifecycleEvent({ status: "noop", success: true }), null);
});

test("older frontends fall back to the appropriate console level", () => {
    const failedLogger = fakeLogger();
    const deferredLogger = fakeLogger();

    assert.equal(
        showLifecycleNotice({ success: false, message: "legacy failure" }, { logger: failedLogger }),
        "console",
    );
    assert.equal(failedLogger.calls.error.length, 1);
    assert.match(failedLogger.calls.error[0][0], /legacy failure/);

    assert.equal(
        showLifecycleNotice({ status: "deferred", success: false }, { logger: deferredLogger }),
        "console",
    );
    assert.equal(deferredLogger.calls.error.length, 0);
    assert.equal(deferredLogger.calls.info.length, 1);
});

test("a broken toast implementation degrades to console", () => {
    const logger = fakeLogger();
    const channel = showLifecycleNotice(
        { status: "failed", success: false },
        {
            appRef: {
                extensionManager: {
                    toast: {
                        add() {
                            throw new Error("toast unavailable");
                        },
                    },
                },
            },
            logger,
        },
    );

    assert.equal(channel, "console");
    assert.equal(logger.calls.warn.length, 1);
    assert.equal(logger.calls.error.length, 1);
});
