import { api } from "../../scripts/api.js";
import { app } from "../../scripts/app.js";

app.registerExtension({
    name: "llamacpp.LifecycleEvents",
    setup() {
        api.addEventListener("llamacpp.lifecycle", (event) => {
            const detail = event?.detail ?? {};
            if (detail.success !== false) return;
            const message = detail.message ?? "llama.cpp resource release failed";
            console.error(`[llama.cpp] ${message}`, detail);
        });
    },
});
