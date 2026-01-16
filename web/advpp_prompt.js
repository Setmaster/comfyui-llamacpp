import { app } from "../../scripts/app.js";

// Templates loaded from JSON file
let TEMPLATES = null;

// Load templates from JSON file
async function loadTemplates() {
    if (TEMPLATES !== null) {
        return TEMPLATES;
    }

    try {
        const response = await fetch("/extensions/comfyui-llamacpp/templates.json");
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        TEMPLATES = await response.json();
        console.log("[llama.cpp] Loaded templates:", Object.keys(TEMPLATES));
        return TEMPLATES;
    } catch (error) {
        console.warn("[llama.cpp] Could not load templates.json:", error);
        // Fallback to empty template
        TEMPLATES = { "Empty": { "system_prompt": "", "prompt": "" } };
        return TEMPLATES;
    }
}

// Pre-load templates
loadTemplates();

app.registerExtension({
    name: "llamacpp.AdvPPPrompt",
    async beforeRegisterNodeDef(nodeType, nodeData, app) {
        if (nodeData.name === "LlamaCppAdvPPPrompt") {
            const onNodeCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                onNodeCreated?.apply(this, []);

                // Find widgets
                const templateWidget = this.widgets?.find(w => w.name === "template");
                const imageAmountWidget = this.widgets?.find(w => w.name === "image_amount");
                const systemPromptWidget = this.widgets?.find(w => w.name === "system_prompt");
                const promptWidget = this.widgets?.find(w => w.name === "prompt");

                // ==================== Template Handling ====================
                if (templateWidget && systemPromptWidget && promptWidget) {
                    // Store the original callback
                    const originalTemplateCallback = templateWidget.callback;

                    // Flag to track if this is user-initiated change
                    this._isUserTemplateChange = true;

                    templateWidget.callback = async (value, ...args) => {
                        // Only auto-fill if this is a user-initiated change
                        if (this._isUserTemplateChange) {
                            const templates = await loadTemplates();
                            if (templates[value]) {
                                const template = templates[value];
                                systemPromptWidget.value = template.system_prompt;
                                promptWidget.value = template.prompt;

                                // Trigger redraw
                                app.graph.setDirtyCanvas(true, true);
                            }
                        }

                        if (originalTemplateCallback) {
                            originalTemplateCallback.call(this, value, ...args);
                        }
                    };
                }

                // ==================== Dynamic Image Inputs ====================
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
                // Disable auto-fill during workflow load
                this._isUserTemplateChange = false;

                onConfigure?.apply(this, [info]);

                // Find widgets
                const imageAmountWidget = this.widgets?.find(w => w.name === "image_amount");

                // ==================== Restore Image Inputs ====================
                if (imageAmountWidget) {
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
                }

                // Re-enable auto-fill for future user changes
                // Use setTimeout to ensure this happens after all configure operations
                setTimeout(() => {
                    this._isUserTemplateChange = true;
                }, 0);
            };
        }
    },
});
