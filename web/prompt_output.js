import { app } from "../../scripts/app.js";
import { ComfyWidgets } from "../../scripts/widgets.js";

app.registerExtension({
    name: "llamacpp.PromptOutput",
    async beforeRegisterNodeDef(nodeType, nodeData, app) {
        if (nodeData.name === "LlamaCppPromptOutput") {
            const onNodeCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                onNodeCreated ? onNodeCreated.apply(this, []) : undefined;

                // Create a multiline text widget to display output
                this.showValueWidget = ComfyWidgets["STRING"](
                    this,
                    "output",
                    ["STRING", { multiline: true }],
                    app
                ).widget;
                this.showValueWidget.inputEl.readOnly = true;
                this.showValueWidget.inputEl.style.opacity = 0.8;

                // Don't serialize the display widget value
                this.showValueWidget.serializeValue = async () => "";
            };

            // Handle executed result to update the display
            const onExecuted = nodeType.prototype.onExecuted;
            nodeType.prototype.onExecuted = function (message) {
                onExecuted?.apply(this, [message]);
                if (message.text && message.text[0] !== undefined) {
                    this.showValueWidget.value = message.text[0];
                }
            };
        }
    },
});
