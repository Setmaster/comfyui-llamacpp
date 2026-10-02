import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { openProfileEditor, readImportedProfiles } from "../../web/task_profile_editor.js";
import {
    canonicalProfileJSON, createAutomaticProfileLoader, mergeProfileDocuments,
    normalizeTaskProfile, parseProfileDocument, profileDocumentJSON,
} from "../../web/task_profile_state.js";

function profile(overrides = {}) {
    return {
        schema_version: 1, id: "author", name: "Author", description: "",
        system_prompt: "", prompt_prefix: "", prompt_suffix: "", ...overrides,
    };
}
const freeform = profile({ id: "freeform", name: "Freeform", description: "No prompt transformation." });
const revision = "a".repeat(64);
const nextRevision = "b".repeat(64);
const response = (profiles = [], hash = revision) => ({
    ok: true, json: async () => ({ schema_version: 1, profiles, content_sha256: hash }),
});
function deferred() {
    let resolve;
    const promise = new Promise((done) => { resolve = done; });
    return { promise, resolve };
}
const flush = () => new Promise((done) => setImmediate(done));

/** Minimal DOM boundary; tests exercise the real editor and extension handlers. */
class Element {
    constructor(tag, doc) {
        this.tagName = tag; this.doc = doc; this.children = []; this.listeners = new Map();
        this.attributes = {}; this.style = {}; this.value = ""; this.disabled = false;
        this._text = "";
    }
    set textContent(value) { this._text = value; this.children = []; }
    get textContent() { return this._text; }
    append(...items) { for (const item of items) { this.children.push(item); item.parent = this; } }
    replaceChildren(...items) { this.children = []; this.append(...items); }
    setAttribute(key, value) { this.attributes[key] = value; }
    addEventListener(name, callback) {
        const list = this.listeners.get(name) ?? []; list.push(callback); this.listeners.set(name, list);
    }
    async emit(name) {
        for (const callback of this.listeners.get(name) ?? []) {
            await callback({ target: this, preventDefault() {} });
        }
    }
    click() { return this.disabled ? Promise.resolve() : this.emit("click"); }
    focus() { this.doc.activeElement = this; }
    showModal() { this.open = true; }
    close() { this.open = false; }
    remove() { if (this.parent) this.parent.children = this.parent.children.filter((item) => item !== this); }
}
class Document {
    constructor() { this.body = new Element("body", this); this.activeElement = new Element("button", this); }
    createElement(tag) { return new Element(tag, this); }
    all() {
        const result = [];
        function walk(item) { result.push(item); item.children.forEach(walk); }
        walk(this.body); return result;
    }
    button(label) { return this.all().find((item) => item.tagName === "button" && item.textContent === label); }
    input(name) { return this.all().find((item) => item.name === name); }
    notice() { return this.all().find((item) => item.attributes["aria-live"] === "polite").textContent; }
}
async function editorHarness({ profiles = [], save, get, snapshot = freeform } = {}) {
    const doc = new Document();
    const requests = [];
    const saved = [];
    const api = {
        async fetchApi(url, options) {
            requests.push({ url, options });
            if (options?.method === "POST") {
                return save ? save(options) : response(JSON.parse(options.body).profiles, nextRevision);
            }
            return get ? get() : response(profiles);
        },
    };
    const editor = openProfileEditor({
        api, snapshotText: canonicalProfileJSON(snapshot), selectedId: snapshot.id,
        document: doc, onSaved: (...args) => saved.push(args),
    });
    await editor.ready;
    return { editor, doc, requests, saved, api };
}
async function input(doc, name, value) {
    const element = doc.input(name); assert.ok(element); assert.equal(element.disabled, false);
    element.value = value; await element.emit("input");
}
function importFile(value) {
    const bytes = typeof value === "string" ? new TextEncoder().encode(value) : value;
    return { size: bytes.length, arrayBuffer: async () => bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) };
}
async function importInto(doc, value) {
    const field = doc.all().find((item) => item.type === "file");
    field.files = [importFile(value)]; await field.emit("change"); await flush();
}

test("versioned import/export preserves exact text and rejects invalid Unicode, duplicate keys and Freeform", async () => {
    const entry = profile({ name: " 文✨ ", system_prompt: "\r\n  instructions\t", prompt_prefix: " \t", prompt_suffix: "\n尾 " });
    const encoded = profileDocumentJSON([entry]);
    assert.deepEqual((await readImportedProfiles(importFile(encoded))).profiles, [entry]);
    assert.throws(() => parseProfileDocument(encoded.replace('"id":"author"', '"id":"author","\\u0069d":"other"')), /duplicate keys/);
    assert.throws(() => profileDocumentJSON([freeform]), /Freeform/);
    assert.throws(() => profileDocumentJSON([entry, entry]), /Duplicate profile ID/);
    assert.throws(() => parseProfileDocument('{"schema_version":2,"profiles":[]}'), /version 1/);
    await assert.rejects(readImportedProfiles(importFile(new Uint8Array([0xff]))), /UTF-8/);
    await assert.rejects(readImportedProfiles(importFile("\ufeff" + encoded)), /valid JSON/);
    await assert.rejects(readImportedProfiles({ size: 1048577 }), /1 MiB/);
    assert.equal(normalizeTaskProfile(profile({ prompt_prefix: "\ud800" })), null);
    assert.ok(normalizeTaskProfile(profile({ name: "✨".repeat(128) })));
    assert.ok(normalizeTaskProfile(profile({ system_prompt: "😀".repeat(65536 - 100) })));
    assert.equal(normalizeTaskProfile(profile({ system_prompt: "😀".repeat(65536), prompt_prefix: "x".repeat(1000) })), null);
});

test("import collisions require an explicit replacement choice and preserve input documents", () => {
    const existing = [profile({ name: "Keep" })];
    const incoming = [profile({ name: "Replace" }), profile({ id: "second" })];
    const keep = mergeProfileDocuments(existing, incoming);
    assert.deepEqual(keep.conflicts, ["author"]);
    assert.equal(keep.document.profiles[0].name, "Keep");
    const replace = mergeProfileDocuments(existing, incoming, { replaceExisting: true });
    assert.equal(replace.document.profiles[0].name, "Replace");
    assert.equal(existing[0].name, "Keep");
    assert.throws(() => mergeProfileDocuments(
        Array.from({ length: 128 }, (_, i) => profile({ id: `id-${i}` })), [profile()],
    ), /128/);
});

test("real dialog edits are drafts; Save Library sends exact text with revision and leaves snapshot untouched", async () => {
    const harness = await editorHarness();
    const { doc, editor } = harness;
    assert.equal(doc.input("id").disabled, true);
    assert.equal(doc.all().find((item) => item.tagName === "dialog").attributes["aria-labelledby"].startsWith("llamacpp-profile-editor-"), true);
    assert.ok(doc.all().some((item) => item.tagName === "summary" && /saved workflow snapshot/.test(item.textContent)));
    await doc.button("New Profile").click();
    await input(doc, "id", "unicode");
    await input(doc, "name", " 中文 ✨ ");
    await input(doc, "system_prompt", "\n Keep exact whitespace.  \t");
    await input(doc, "prompt_prefix", " \t");
    await input(doc, "prompt_suffix", "\n終 ");
    assert.equal(harness.requests.length, 1);
    await doc.button("Save Library").click();
    assert.equal(harness.requests.length, 2);
    const sent = harness.requests[1].options;
    assert.equal(sent.headers["If-Match"], `"${revision}"`);
    assert.equal(JSON.parse(sent.body).profiles[0].system_prompt, "\n Keep exact whitespace.  \t");
    assert.equal(harness.saved[0][0], "unicode");
    assert.deepEqual(harness.saved[0][1][0], freeform);
    assert.match(doc.notice(), /Workflow snapshot unchanged/);
    editor.dispose();
    assert.equal(doc.body.children.length, 0);
});

test("conflicting save retains edited fields and old revision, without reporting success", async () => {
    const harness = await editorHarness({
        profiles: [profile()], snapshot: profile(),
        save: async () => ({ ok: false, status: 409, json: async () => ({}) }),
    });
    await input(harness.doc, "prompt_prefix", "unsaved text  ");
    await harness.doc.button("Save Library").click();
    assert.match(harness.doc.notice(), /changed in another editor/);
    assert.equal(harness.doc.input("prompt_prefix").value, "unsaved text  ");
    assert.equal(harness.saved.length, 0);
    await harness.doc.button("Save Library").click();
    assert.equal(harness.requests.at(-1).options.headers["If-Match"], `"${revision}"`);
    harness.editor.dispose();
});

test("real import preview names collisions and only explicit merge plus save persists them", async () => {
    const harness = await editorHarness({ profiles: [profile({ name: "Original" })] });
    await importInto(harness.doc, profileDocumentJSON([profile({ name: "Imported" })]));
    assert.ok(harness.doc.all().some((item) => /Matching IDs: author/.test(item.textContent)));
    assert.equal(harness.requests.length, 1);
    await harness.doc.button("Replace matching IDs and import").click();
    assert.equal(harness.requests.length, 1);
    await harness.doc.button("Save Library").click();
    assert.equal(JSON.parse(harness.requests[1].options.body).profiles[0].name, "Imported");
    harness.editor.dispose();
});

test("import preview permits a valid replacement even when keeping existing text would exceed the library limit", async () => {
    const large = "x".repeat(65000);
    const existing = Array.from({ length: 5 }, (_, index) => profile({
        id: `profile-${index}`, system_prompt: large, prompt_prefix: large, prompt_suffix: large,
    }));
    const extra = "y".repeat(35000);
    const incoming = [profile({ id: "profile-0" }), profile({
        id: "added", system_prompt: extra, prompt_prefix: extra, prompt_suffix: extra,
    })];
    const harness = await editorHarness({ profiles: existing });
    await importInto(harness.doc, profileDocumentJSON(incoming));
    assert.equal(harness.doc.button("Replace matching IDs and import").disabled, false);
    await harness.doc.button("Import new IDs only").click();
    assert.match(harness.doc.notice(), /1 MiB/);
    assert.equal(harness.requests.length, 1);
    await harness.doc.button("Replace matching IDs and import").click();
    await harness.doc.button("Save Library").click();
    const saved = JSON.parse(harness.requests[1].options.body).profiles;
    assert.equal(saved.length, 6);
    assert.equal(saved[0].system_prompt, "");
    assert.equal(saved[5].id, "added");
    harness.editor.dispose();
});

test("invalid import keeps current fields and cannot leave a stale prior import enabled", async () => {
    const harness = await editorHarness({ profiles: [profile()], snapshot: profile() });
    await importInto(harness.doc, profileDocumentJSON([profile({ id: "new" })]));
    assert.equal(harness.doc.button("Import new IDs only").disabled, false);
    await importInto(harness.doc, '{"schema_version":1,"profiles":[],"profiles":[]}');
    assert.match(harness.doc.notice(), /duplicate keys/);
    assert.equal(harness.doc.button("Import new IDs only").disabled, true);
    assert.equal(harness.doc.input("id").value, "author");
    assert.equal(harness.requests.length, 1);
    harness.editor.dispose();
});

test("disposing an editor during a delayed save prevents stale callbacks", async () => {
    const pending = deferred();
    const harness = await editorHarness({ profiles: [profile()], snapshot: profile(), save: () => pending.promise });
    const saving = harness.doc.button("Save Library").click();
    harness.editor.dispose();
    pending.resolve(response([profile()], nextRevision));
    await saving;
    assert.equal(harness.saved.length, 0);
    assert.equal(harness.doc.body.children.length, 0);
});

test("saving invalidates cached/in-flight reads without allowing their late result into the cache", async () => {
    const pending = deferred();
    let calls = 0;
    const load = createAutomaticProfileLoader(() => ++calls === 1 ? pending.promise : Promise.resolve("saved"));
    const old = load({ automatic: true });
    load.invalidate();
    assert.equal(await load({ automatic: true }), "saved");
    pending.resolve("old"); await old;
    assert.equal(await load({ automatic: true }), "saved");
});

let extensionSerial = 0;
async function extensionHarness(t, { delayedProfiles = null } = {}) {
    const doc = new Document();
    const previousDocument = globalThis.document;
    globalThis.document = doc;
    t.after(() => { globalThis.document = previousDocument; });
    const original = '{ "schema_version":1,"id":"author","name":"Saved","description":"", "system_prompt":"  old ","prompt_prefix":"", "prompt_suffix":"" }';
    let library = [profile({ name: "Local" })];
    const undo = [];
    const graph = { beforeChange() { undo.push(node.widgets[0].value); }, afterChange() {}, setDirtyCanvas() {} };
    const api = { async fetchApi(url, options) {
        if (url.endsWith("/library")) {
            if (options?.method === "POST") library = JSON.parse(options.body).profiles;
            return response(library, options?.method === "POST" ? nextRevision : revision);
        }
        if (delayedProfiles) return delayedProfiles.promise;
        return { ok: true, json: async () => ({ schema_version: 1, profiles: [freeform, ...library] }) };
    } };
    const app = { graph, registerExtension() {} };
    const node = {
        constructor: { comfyClass: "LlamaCppTaskProfile" }, graph, widgets: [{ name: "profile_snapshot", value: original, options: {} }],
        size: [360, 100], computeSize() { return this.size; }, setSize() {},
        addWidget(type, name, value, callback, options = {}) {
            const item = { type, name, value, callback, options }; this.widgets.push(item); return item;
        },
    };
    const url = new URL("../../web/task_profile.js", import.meta.url);
    let source = await readFile(url, "utf8");
    const key = `__profileExtension${++extensionSerial}`;
    globalThis[key] = { api, app };
    for (const name of ["api", "app"]) source = source.replace(`import { ${name} } from "../../scripts/${name}.js";`, `const { ${name} } = globalThis[${JSON.stringify(key)}];`);
    source = source.replaceAll(/"\.\/(generate_controls|task_profile_state|task_profile_editor)\.js"/g, (_, name) => JSON.stringify(new URL(`${name}.js`, url).href));
    const module = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
    delete globalThis[key];
    module.setupTaskProfileNode(node, { refresh: false });
    return { doc, node, undo, original };
}

test("actual extension library editing never changes the snapshot; explicit Update is undoable and actions do not serialize", async (t) => {
    // Node has no confirm browser API, so supply it only in the isolated UI harness.
    const previousConfirm = globalThis.confirm;
    globalThis.confirm = () => true;
    t.after(() => { globalThis.confirm = previousConfirm; });
    const h = await extensionHarness(t);
    const state = h.node.__llamacppProfileState;
    state.editButton.callback();
    await state.editor.ready;
    await input(h.doc, "system_prompt", "  new\n指示 ");
    await h.doc.button("Save Library").click();
    assert.equal(state.snapshot.value, h.original);
    assert.deepEqual(h.undo, []);
    assert.match(state.status.value, /local profile changed/);
    state.updateButton.callback();
    assert.equal(JSON.parse(state.snapshot.value).system_prompt, "  new\n指示 ");
    assert.deepEqual(h.undo, [h.original]);
    state.snapshot.value = h.undo.pop();
    assert.equal(state.snapshot.value, h.original);
    for (const widget of h.node.widgets.slice(1)) {
        assert.equal(widget.serialize, false);
        assert.equal(widget.serializeValue(), undefined);
    }
    h.node.onRemoved();
    assert.equal(h.doc.body.children.length, 0);
});


test("real Export JSON downloads a versioned user-only draft without saving", async (t) => {
    const h = await editorHarness({ profiles: [profile()], snapshot: profile() });
    let exported;
    t.mock.method(URL, "createObjectURL", (blob) => { exported = blob; return "blob:profile-test"; });
    t.mock.method(URL, "revokeObjectURL", () => {});
    await input(h.doc, "prompt_suffix", "  \n尾\t");
    await h.doc.button("Export JSON").click();
    const parsed = parseProfileDocument(await exported.text());
    assert.equal(parsed.profiles[0].prompt_suffix, "  \n尾\t");
    assert.equal(parsed.profiles.some((entry) => entry.id === "freeform"), false);
    assert.equal(h.requests.length, 1);
    h.editor.dispose();
});

test("missing local profile snapshot can be inspected and copied without mutation", async () => {
    const snapshot = profile({ id: "missing", name: "Portable", prompt_prefix: "  preserved\n" });
    const h = await editorHarness({ snapshot });
    assert.ok(h.doc.all().some((entry) => entry.tagName === "pre" && entry.textContent.includes('"missing"')));
    await h.doc.button("Copy snapshot to new profile").click();
    assert.equal(h.doc.input("id").value, "profile-1");
    assert.equal(h.doc.input("prompt_prefix").value, snapshot.prompt_prefix);
    assert.equal(h.requests.length, 1);
    h.editor.dispose();
});

test("actual extension rejects a stale profile refresh after acknowledged library save", async (t) => {
    const pending = deferred();
    const h = await extensionHarness(t, { delayedProfiles: pending });
    const state = h.node.__llamacppProfileState;
    const refreshing = state.refreshButton.callback();
    state.editButton.callback(); await state.editor.ready;
    await input(h.doc, "name", "Saved newer");
    await h.doc.button("Save Library").click();
    pending.resolve({ ok: true, json: async () => ({ schema_version: 1, profiles: [freeform] }) });
    await refreshing;
    assert.equal(state.profiles.find((entry) => entry.id === "author").name, "Saved newer");
    assert.equal(state.selector.value, "author");
    assert.equal(state.snapshot.value, h.original);
    h.node.onRemoved();
});
