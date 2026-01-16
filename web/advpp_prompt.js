import { app } from "../../scripts/app.js";

// Template definitions - must match Python TEMPLATES
const TEMPLATES = {
    "Empty": {
        "system_prompt": "",
        "prompt": ""
    },
    "Image2Prompt": {
        "system_prompt": `Write a single, final image-generation prompt for an image.
In a single concise paragraph describe:

1) The main subject: age, ethnicity, body type and proportions
2) Clothing and accessories, fashion styles
3) Body posture, pose, and  position in frame
4) Physical attributes for each character such as skin color, hair color, eye color and ethnicity
4) Environment, time of day, and background
5) focal length, lighting, angle, focus, exposure, framing

Rules:
1. Focus on recreating the original composition, capturing the details that make this image unique and interesting. prioritize capturing any compositional details or anomalies.
2. Never use words like: appears, seems, looks like, likely, possibly.
3. Do not omit nudity or anatomy where it is visible.
4. Do not include watermarks, urls or signatures.
5. Output only the photographic prompt. End after the last sentence.
6. Use clear, direct, factual sentences.
8. Do not omit text unless fit conflicts with rule 4.
9. If the image is not photorealistic then make sure to mold the prompt according to its artstyle`,
        "prompt": "output:"
    },
    "Prompt Enhancer": {
        "system_prompt": `You are an expert prompt engineer for image generation models. Your task is to transform rough, unpolished prompts into detailed, high-quality image generation prompts.

When given a basic prompt, enhance it by:
1) Adding specific visual details: colors, textures, materials, lighting conditions
2) Specifying composition: framing, perspective, focal point, depth of field
3) Including style descriptors: artistic style, mood, atmosphere, quality tags
4) Adding technical photography terms when appropriate: lens type, aperture, exposure
5) Maintaining the original intent while elevating the descriptive quality

Rules:
1. Output ONLY the enhanced prompt, nothing else
2. Keep the enhanced prompt as a single flowing paragraph
3. Do not add explanations or commentary
4. Do not use markdown formatting
5. Preserve the core subject and intent of the original prompt
6. Be specific and vivid, avoid vague or generic terms
7. Do not exceed 200 words unless necessary for complex scenes`,
        "prompt": ""
    }
};

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

                    templateWidget.callback = (value, ...args) => {
                        // Only auto-fill if this is a user-initiated change
                        if (this._isUserTemplateChange && TEMPLATES[value]) {
                            const template = TEMPLATES[value];
                            systemPromptWidget.value = template.system_prompt;
                            promptWidget.value = template.prompt;

                            // Trigger redraw
                            app.graph.setDirtyCanvas(true, true);
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

                    // Trigger node resize
                    this.setSize(this.computeSize());
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
