import assert from "node:assert/strict";
import test from "node:test";

import {
    canonicalProfileJSON,
    createAutomaticProfileLoader,
    normalizeProfilesResponse,
    parseProfileSnapshot,
    profileSnapshotStatus,
    reconcileProfileSelection,
} from "../../web/task_profile_state.js";

function profile(overrides = {}) {
    return {
        schema_version: 1,
        id: "freeform",
        name: "Freeform",
        description: "No transform",
        system_prompt: "",
        prompt_prefix: "",
        prompt_suffix: "",
        ...overrides,
    };
}

test("profile snapshots use deterministic Python-compatible key order", () => {
    assert.equal(
        canonicalProfileJSON(profile()),
        '{"description":"No transform","id":"freeform","name":"Freeform",' +
            '"prompt_prefix":"","prompt_suffix":"","schema_version":1,"system_prompt":""}',
    );
    assert.deepEqual(parseProfileSnapshot(canonicalProfileJSON(profile())), profile());
});

test("profile snapshots reject duplicate keys, including escaped aliases", () => {
    const base = canonicalProfileJSON(profile());
    assert.equal(
        parseProfileSnapshot(base.replace('"id":"freeform"', '"id":"freeform","id":"caption"')),
        null,
    );
    assert.equal(
        parseProfileSnapshot(
            base.replace('"id":"freeform"', '"id":"freeform","\\u0069d":"caption"'),
        ),
        null,
    );
});

test("automatic profile reads share in-flight work and a short cache while manual reads bypass", async () => {
    let now = 100;
    let calls = 0;
    const load = createAutomaticProfileLoader(
        async () => ({ value: ++calls }),
        { clock: () => now, cacheMilliseconds: 1000 },
    );

    const [first, shared] = await Promise.all([
        load({ automatic: true }),
        load({ automatic: true }),
    ]);
    assert.deepEqual(first, { value: 1 });
    assert.equal(shared, first);
    assert.equal(calls, 1);

    assert.equal(await load({ automatic: true }), first);
    assert.equal(calls, 1);
    const manual = await load();
    assert.deepEqual(manual, { value: 2 });
    assert.equal(calls, 2);
    assert.equal(await load({ automatic: true }), manual);

    now = 1101;
    assert.deepEqual(await load({ automatic: true }), { value: 3 });
    assert.equal(calls, 3);
});

test("failed automatic profile reads clear the in-flight slot for retry", async () => {
    let calls = 0;
    const load = createAutomaticProfileLoader(async () => {
        calls += 1;
        if (calls === 1) throw new Error("temporary");
        return "ok";
    });

    await assert.rejects(load({ automatic: true }), /temporary/);
    assert.equal(await load({ automatic: true }), "ok");
    assert.equal(calls, 2);
});

test("profile response normalization rejects duplicates and malformed content", () => {
    const result = normalizeProfilesResponse({
        schema_version: 1,
        profiles: [
            profile(),
            profile({ name: "Duplicate" }),
            profile({ id: "caption", name: "Caption" }),
            { arbitrary: true },
        ],
    });
    assert.deepEqual(
        result.map((item) => item.id),
        ["freeform", "caption"],
    );
    assert.deepEqual(normalizeProfilesResponse({ profiles: [] }), []);
});

test("status distinguishes current, changed, missing, and invalid snapshots", () => {
    const snapshot = canonicalProfileJSON(profile());
    assert.deepEqual(profileSnapshotStatus(snapshot, [profile()]), {
        kind: "current",
        text: "Saved: Freeform (current)",
    });
    assert.equal(
        profileSnapshotStatus(snapshot, [profile({ description: "changed" })]).kind,
        "changed",
    );
    assert.equal(profileSnapshotStatus(snapshot, []).kind, "missing");
    assert.equal(profileSnapshotStatus("not json", [profile()]).kind, "invalid");
});

test("graph-load reconciliation restores the saved profile without overwriting user input", () => {
    const saved = canonicalProfileJSON(profile({ id: "caption", name: "Caption" }));
    const profiles = [profile(), profile({ id: "caption", name: "Caption" })];

    assert.equal(reconcileProfileSelection(saved, profiles, "freeform"), "caption");
    assert.equal(
        reconcileProfileSelection(saved, profiles, "freeform", true),
        "freeform",
    );
    assert.equal(reconcileProfileSelection(saved, [profile()], "freeform"), "freeform");
});
