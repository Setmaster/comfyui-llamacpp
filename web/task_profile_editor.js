import {
    FREEFORM_PROFILE_ID,
    parseProfileDocument,
    parseProfileSnapshot,
    profileDocumentJSON,
    mergeProfileDocuments,
} from "./task_profile_state.js";

const ROUTE = "/llamacpp/profiles/library";
const FREEFORM = {
    schema_version: 1, id: FREEFORM_PROFILE_ID, name: "Freeform",
    description: "No prompt transformation.", system_prompt: "", prompt_prefix: "", prompt_suffix: "",
};
const FIELDS = [
    ["id", "Profile ID", false], ["name", "Name", false],
    ["description", "Description", true], ["system_prompt", "System prompt", true],
    ["prompt_prefix", "Prompt prefix", true], ["prompt_suffix", "Prompt suffix", true],
];
let editorSerial = 0;

export function normalizeLibraryResponse(payload) {
    if (!payload || !/^[a-f0-9]{64}$/.test(payload.content_sha256 ?? "")) {
        throw new Error("The library response has no valid save revision.");
    }
    return {
        document: parseProfileDocument(JSON.stringify({
            schema_version: payload.schema_version, profiles: payload.profiles,
        })),
        revision: payload.content_sha256,
    };
}

export async function readImportedProfiles(file) {
    if (!file || file.size > 1048576) throw new Error("Profile file must be at most 1 MiB.");
    let text;
    try {
        // File.text() would silently replace invalid UTF-8 bytes.
        text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(await file.arrayBuffer());
    } catch {
        throw new Error("Profile file must use valid UTF-8.");
    }
    return parseProfileDocument(text);
}

/** Edits are local drafts until Save Library; no workflow widget is passed here. */
export function openProfileEditor({
    api, snapshotText, selectedId = FREEFORM_PROFILE_ID, onSaved = () => {}, onClose = () => {},
    document: doc = globalThis.document,
}) {
    const ownerFocus = doc.activeElement;
    const identity = `llamacpp-profile-editor-${++editorSerial}`;
    const dialog = doc.createElement("dialog");
    dialog.className = "llamacpp-profile-editor";
    dialog.setAttribute("aria-labelledby", `${identity}-title`);
    const style = doc.createElement("style");
    style.textContent = `
.llamacpp-profile-editor { box-sizing: border-box; width: min(760px, 94vw); max-height: 90vh;
 padding: 20px; overflow: auto; color: var(--input-text, #eee); background: var(--comfy-menu-bg, #242424);
 border: 1px solid var(--border-color, #666); border-radius: 8px; }
.llamacpp-profile-editor::backdrop { background: #0009; }
.llamacpp-profile-editor h2 { margin: 0 0 8px; font-size: 1.2rem; }
.llamacpp-profile-editor p { line-height: 1.45; }
.llamacpp-profile-editor label { display: grid; gap: 5px; margin: 10px 0; }
.llamacpp-profile-editor input, .llamacpp-profile-editor textarea, .llamacpp-profile-editor select {
 box-sizing: border-box; width: 100%; color: inherit; background: var(--comfy-input-bg, #171717);
 border: 1px solid var(--border-color, #777); border-radius: 4px; padding: 7px; font: inherit; }
.llamacpp-profile-editor textarea { min-height: 70px; resize: vertical; white-space: pre-wrap; }
.llamacpp-profile-editor button { color: inherit; background: var(--comfy-input-bg, #333);
 border: 1px solid var(--border-color, #888); border-radius: 4px; padding: 8px 12px; cursor: pointer; }
.llamacpp-profile-editor button:disabled { opacity: .5; cursor: default; }
.llamacpp-profile-editor :focus-visible { outline: 2px solid #79b8ff; outline-offset: 2px; }
.llamacpp-profile-editor .actions { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0; }
.llamacpp-profile-editor .notice { white-space: pre-wrap; overflow-wrap: anywhere; min-height: 1.5em; }
.llamacpp-profile-editor pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 240px; overflow: auto; }
.llamacpp-profile-editor summary { cursor: pointer; padding: 8px 0; }
`;
    dialog.append(style);
    function element(tag, text, parent = dialog) {
        const item = doc.createElement(tag);
        if (text !== undefined) item.textContent = text;
        parent.append(item);
        return item;
    }
    const title = element("h2", "Task profile library");
    title.id = `${identity}-title`;
    element("p", "Edit your local profiles here. Save Library does not change this workflow. Use Update Saved Snapshot on the node to apply a saved profile.");
    const notice = element("p", "Loading library...");
    notice.className = "notice";
    notice.setAttribute("role", "status");
    notice.setAttribute("aria-live", "polite");
    const snapshot = element("details");
    element("summary", "Inspect saved workflow snapshot", snapshot);
    const saved = parseProfileSnapshot(snapshotText);
    element("pre", saved ? JSON.stringify(saved, null, 2) : "The saved snapshot is invalid.", snapshot);
    const snapshotActions = element("div", undefined, snapshot);
    snapshotActions.className = "actions";
    const selectionLabel = element("label", "Local profile");
    const selector = element("select", undefined, selectionLabel);
    selector.setAttribute("aria-label", "Local profile to edit");
    const actions = element("div");
    actions.className = "actions";
    const form = element("div");
    const inputs = new Map();
    for (const [key, label, multiline] of FIELDS) {
        const wrapper = element("label", label, form);
        const input = element(multiline ? "textarea" : "input", undefined, wrapper);
        input.id = `${identity}-${key}`;
        input.name = key;
        input.spellcheck = key !== "id";
        inputs.set(key, input);
        input.addEventListener("input", () => {
            if (busy || selected < 0) return;
            profiles[selected][key] = input.value;
            dirty = true;
            showNotice("Unsaved library edits. The workflow snapshot is unchanged.");
        });
    }
    const importArea = element("div");
    const importNotice = element("p", "", importArea);
    importNotice.className = "notice";
    const importActions = element("div", undefined, importArea);
    importActions.className = "actions";
    const footer = element("div");
    footer.className = "actions";
    const buttons = [];
    function button(label, callback, parent = actions) {
        const item = element("button", label, parent);
        item.type = "button";
        item.addEventListener("click", callback);
        buttons.push(item);
        return item;
    }
    let profiles = [];
    let revision = null;
    let selected = -1;
    let dirty = false;
    let busy = false;
    let closed = false;
    let imported = null;
    function showNotice(text, error = false) {
        notice.textContent = text;
        notice.setAttribute("role", error ? "alert" : "status");
    }
    function controls() {
        for (const item of buttons) item.disabled = busy || !revision;
        closeButton.disabled = busy;
        reloadButton.disabled = busy;
        selector.disabled = busy || !revision;
        removeButton.disabled = busy || selected < 0;
        fromSnapshot.disabled = busy || !revision || !saved;
        importNew.disabled = busy || !imported;
        importReplace.disabled = busy || !imported;
        importArea.hidden = !imported;
        for (const input of inputs.values()) input.disabled = busy || !revision || selected < 0;
    }
    function render() {
        selector.replaceChildren();
        const builtin = element("option", "Freeform (built in, read only)", selector);
        builtin.value = "-1";
        profiles.forEach((profile, index) => {
            const option = element("option", `${profile.name} (${profile.id})`, selector);
            option.value = String(index);
        });
        selector.value = String(selected);
        const profile = profiles[selected] ?? FREEFORM;
        for (const [key, input] of inputs) input.value = profile[key];
        controls();
    }
    async function operation(callback) {
        if (closed || busy) return;
        busy = true;
        controls();
        try { await callback(); }
        catch (error) { if (!closed) showNotice(error?.message ?? String(error), true); }
        finally { busy = false; if (!closed) controls(); }
    }
    async function responsePayload(response) {
        const payload = await response.json();
        if (!response.ok) {
            throw new Error(response.status === 409
                ? "Library changed in another editor. Export your edits, then reload before saving."
                : payload?.error?.message ?? `Library request failed (HTTP ${response.status}).`);
        }
        return normalizeLibraryResponse(payload);
    }
    function load() {
        return operation(async () => {
            const loaded = await responsePayload(await api.fetchApi(ROUTE));
            if (closed) return;
            profiles = loaded.document.profiles;
            revision = loaded.revision;
            selected = profiles.findIndex((profile) => profile.id === selectedId);
            dirty = false;
            imported = null;
            importNotice.textContent = "";
            showNotice("Library loaded. Freeform is read only; choose New Profile to write your own.");
            render();
        });
    }
    function addProfile(base = FREEFORM) {
        if (busy || !revision) return;
        if (profiles.length >= 128) { showNotice("The library already contains 128 profiles.", true); return; }
        let suffix = 1;
        while (profiles.some((profile) => profile.id === `profile-${suffix}`)) suffix += 1;
        profiles.push({ ...base, id: `profile-${suffix}`, name: base.id === "freeform" ? "New profile" : `${base.name} copy`, description: base.id === "freeform" ? "" : base.description });
        selected = profiles.length - 1;
        dirty = true;
        render();
        showNotice("New profile is unsaved. Save Library when ready.");
        inputs.get("name").focus();
    }
    selector.addEventListener("change", () => {
        selected = Number(selector.value);
        render();
    });
    button("New Profile", () => addProfile());
    button("Duplicate Profile", () => addProfile(profiles[selected] ?? FREEFORM));
    const removeButton = button("Remove Profile", () => {
        if (busy || selected < 0) return;
        profiles.splice(selected, 1);
        selected = -1;
        dirty = true;
        render();
        showNotice("Profile removed from this draft. Save Library to keep the removal.");
    });
    const fromSnapshot = button("Copy snapshot to new profile", () => addProfile(saved), snapshotActions);
    const fileInput = element("input");
    fileInput.type = "file";
    fileInput.accept = ".json,application/json";
    fileInput.hidden = true;
    fileInput.setAttribute("aria-label", "Import profile JSON file");
    button("Import JSON", () => fileInput.click());
    fileInput.addEventListener("change", () => {
        const file = fileInput.files?.[0];
        fileInput.value = "";
        if (!file) return;
        imported = null;
        importNotice.textContent = "";
        void operation(async () => {
            const incoming = await readImportedProfiles(file);
            // Preview collisions without choosing a merge. Keeping the old
            // entries can exceed the library bound even when replacing them
            // with smaller imported entries produces a valid document.
            const currentIds = new Set(profiles.map((profile) => profile.id));
            const conflicts = incoming.profiles.filter((profile) => currentIds.has(profile.id));
            if (closed) return;
            imported = incoming.profiles;
            importNotice.textContent = `${imported.length} profiles ready to import. Matching IDs: ${conflicts.map((profile) => profile.id).join(", ") || "none"}. Choose how to merge, then Save Library.`;
        });
    });
    function applyImport(replaceExisting) {
        if (!imported || busy) return;
        try {
            profiles = mergeProfileDocuments(profiles, imported, { replaceExisting }).document.profiles;
            selected = -1;
            imported = null;
            dirty = true;
            importNotice.textContent = "";
            showNotice("Import merged into your unsaved draft. Save Library when ready.");
            render();
        } catch (error) { showNotice(error.message, true); }
    }
    const importNew = button("Import new IDs only", () => applyImport(false), importActions);
    const importReplace = button("Replace matching IDs and import", () => applyImport(true), importActions);
    button("Export JSON", () => {
        try {
            const text = profileDocumentJSON(profiles);
            const url = URL.createObjectURL(new Blob([text + "\n"], { type: "application/json" }));
            const anchor = element("a");
            anchor.href = url;
            anchor.download = "llamacpp-task-profiles.json";
            anchor.click();
            anchor.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch (error) { showNotice(error.message, true); }
    });
    button("Save Library", () => operation(async () => {
        const body = profileDocumentJSON(profiles);
        const chosen = profiles[selected]?.id ?? FREEFORM_PROFILE_ID;
        const result = await responsePayload(await api.fetchApi(ROUTE, {
            method: "POST", headers: { "Content-Type": "application/json", "If-Match": `"${revision}"` }, body,
        }));
        if (closed) return;
        profiles = result.document.profiles;
        revision = result.revision;
        dirty = false;
        selectedId = chosen;
        selected = profiles.findIndex((profile) => profile.id === chosen);
        showNotice("Library saved. Workflow snapshot unchanged. Close, then use Update Saved Snapshot to apply it.");
        render();
        onSaved(chosen, [FREEFORM, ...profiles]);
    }), footer);
    const reloadButton = button("Discard edits and reload", load, footer);
    function dispose() {
        if (closed) return;
        closed = true;
        dialog.close();
        dialog.remove();
        ownerFocus?.focus?.();
        onClose();
    }
    function close() {
        if (busy) return;
        if (dirty && !globalThis.confirm("Discard unsaved profile library edits?")) return;
        dispose();
    }
    const closeButton = button("Close", close, footer);
    dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(); });
    doc.body.append(dialog);
    dialog.showModal();
    selector.focus();
    render();
    const ready = load().then(() => { if (!closed && revision) selector.focus(); });
    return { dispose, ready };
}
