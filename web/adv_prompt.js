import { app } from "../../scripts/app.js";

app.registerExtension({
    name: "llamacpp.AdvPrompt",
    async beforeRegisterNodeDef(nodeType, nodeData, app) {
        if (nodeData.name === "LlamaCppAdvPrompt") {
            const onNodeCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                onNodeCreated?.apply(this, []);

                // Find the image_amount widget
                const imageAmountWidget = this.widgets?.find(w => w.name === "image_amount");
                if (!imageAmountWidget) return;

                // Store reference for dynamic input management
                this._imageInputCount = 0;

                // Function to update image inputs based on image_amount value
                const updateImageInputs = (newCount) => {
                    const currentCount = this._imageInputCount;

                    if (newCount > currentCount) {
                        // Add new image inputs
                        for (let i = currentCount + 1; i <= newCount; i++) {
                            this.addInput(`image_${i}`, "IMAGE");
                        }
                    } else if (newCount < currentCount) {
                        // Remove excess image inputs (from the end)
                        for (let i = currentCount; i > newCount; i--) {
                            const inputName = `image_${i}`;
                            const inputIndex = this.inputs?.findIndex(inp => inp.name === inputName);
                            if (inputIndex !== undefined && inputIndex >= 0) {
                                this.removeInput(inputIndex);
                            }
                        }
                    }

                    this._imageInputCount = newCount;

                    // Adjust size: keep width, only grow height if needed
                    const currentSize = this.size;
                    const minSize = this.computeSize();
                    this.setSize([
                        Math.max(currentSize[0], minSize[0]),
                        Math.max(currentSize[1], minSize[1])
                    ]);
                    app.graph.setDirtyCanvas(true, true);
                };

                // Set initial image inputs based on default value
                const initialCount = imageAmountWidget.value || 2;
                updateImageInputs(initialCount);

                // Hook into the widget's callback to detect value changes
                const originalCallback = imageAmountWidget.callback;
                imageAmountWidget.callback = (value, ...args) => {
                    updateImageInputs(value);
                    if (originalCallback) {
                        originalCallback.call(this, value, ...args);
                    }
                };
            };

            // Handle loading saved workflows - restore dynamic inputs
            const onConfigure = nodeType.prototype.onConfigure;
            nodeType.prototype.onConfigure = function (info) {
                onConfigure?.apply(this, [info]);

                // Find saved image_amount value from widgets_values
                const imageAmountWidget = this.widgets?.find(w => w.name === "image_amount");
                if (!imageAmountWidget) return;

                // Get the saved value
                const savedCount = imageAmountWidget.value || 2;

                // Initialize counter if not set
                if (this._imageInputCount === undefined) {
                    this._imageInputCount = 0;
                }

                // Count existing image inputs that were loaded
                const existingImageInputs = this.inputs?.filter(inp =>
                    inp.name && inp.name.startsWith("image_")
                ).length || 0;

                this._imageInputCount = existingImageInputs;

                // Ensure we have the right number of inputs
                if (existingImageInputs !== savedCount) {
                    // Add missing inputs or this will be handled by updateImageInputs
                    const currentCount = this._imageInputCount;

                    if (savedCount > currentCount) {
                        for (let i = currentCount + 1; i <= savedCount; i++) {
                            this.addInput(`image_${i}`, "IMAGE");
                        }
                        this._imageInputCount = savedCount;
                    } else if (savedCount < currentCount) {
                        for (let i = currentCount; i > savedCount; i--) {
                            const inputName = `image_${i}`;
                            const inputIndex = this.inputs?.findIndex(inp => inp.name === inputName);
                            if (inputIndex !== undefined && inputIndex >= 0) {
                                this.removeInput(inputIndex);
                            }
                        }
                        this._imageInputCount = savedCount;
                    }
                }
            };
        }
    },
});
