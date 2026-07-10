const DEFERRED_STATUS = "deferred";
const NON_FAILURE_STATUSES = new Set([DEFERRED_STATUS, "coalesced"]);

function optionalText(value) {
    return typeof value === "string" && value.trim() ? value.trim() : null;
}

export function classifyLifecycleEvent(detail) {
    if (!detail || typeof detail !== "object") return null;

    const status = optionalText(detail.status)?.toLowerCase() ?? "";
    if (status === DEFERRED_STATUS) {
        return {
            severity: "info",
            summary: "llama.cpp release queued",
            detail:
                optionalText(detail.message) ??
                "An active generation is using the runtime. VRAM will be released when it finishes.",
            life: 6000,
        };
    }

    const failed = status === "failed" || (detail.success === false && !NON_FAILURE_STATUSES.has(status));
    if (!failed) return null;

    return {
        severity: "error",
        summary: "llama.cpp release failed",
        detail:
            optionalText(detail.message) ??
            optionalText(detail.error) ??
            "The llama.cpp runtime could not release its resources. Inspect Server Status and local logs.",
        life: 10000,
    };
}

export function showLifecycleNotice(detail, { appRef, logger = console } = {}) {
    const notice = classifyLifecycleEvent(detail);
    if (!notice) return null;

    const toast = appRef?.extensionManager?.toast;
    if (typeof toast?.add === "function") {
        try {
            toast.add(notice);
            return "toast";
        } catch (error) {
            logger?.warn?.("[llama.cpp] Could not show lifecycle notification", error);
        }
    }

    const logMethod = notice.severity === "error" ? "error" : "info";
    logger?.[logMethod]?.(`[llama.cpp] ${notice.summary}: ${notice.detail}`, detail);
    return "console";
}
