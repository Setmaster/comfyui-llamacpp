export function applyTemplateValues(widgets, template) {
    if (!template || typeof template !== "object") return false;
    const systemPrompt = widgets.find((widget) => widget.name === "system_prompt");
    const prompt = widgets.find((widget) => widget.name === "prompt");
    if (!systemPrompt || !prompt) return false;

    systemPrompt.value = String(template.system_prompt ?? "");
    prompt.value = String(template.prompt ?? "");
    return true;
}
