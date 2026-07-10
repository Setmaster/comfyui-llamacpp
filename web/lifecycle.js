import { api } from "../../scripts/api.js";
import { app } from "../../scripts/app.js";
import { showLifecycleNotice } from "./lifecycle_events.js";

app.registerExtension({
    name: "llamacpp.LifecycleEvents",
    setup() {
        api.addEventListener("llamacpp.lifecycle", (event) => {
            showLifecycleNotice(event?.detail ?? {}, { appRef: app, logger: console });
        });
    },
});
