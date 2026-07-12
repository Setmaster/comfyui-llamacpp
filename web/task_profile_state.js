export const FREEFORM_PROFILE_ID = "freeform";

const PROFILE_KEYS = [
    "description",
    "id",
    "name",
    "prompt_prefix",
    "prompt_suffix",
    "schema_version",
    "system_prompt",
];

function boundedText(value, maximum, { allowEmpty = true } = {}) {
    if (typeof value !== "string" || value.length > maximum) return null;
    if (!allowEmpty && value.length === 0) return null;
    return value;
}

function hasDuplicateObjectKeys(source) {
    let offset = 0;

    function whitespace() {
        while (/\s/u.test(source[offset] ?? "")) offset += 1;
    }

    function stringValue() {
        const start = offset;
        offset += 1;
        while (offset < source.length) {
            if (source[offset] === "\\") {
                offset += 2;
                continue;
            }
            if (source[offset] === '"') {
                offset += 1;
                return JSON.parse(source.slice(start, offset));
            }
            offset += 1;
        }
        return null;
    }

    function value() {
        whitespace();
        if (source[offset] === "{") return objectValue();
        if (source[offset] === "[") return arrayValue();
        if (source[offset] === '"') {
            stringValue();
            return false;
        }
        while (offset < source.length && !/[,}\]]/u.test(source[offset])) offset += 1;
        return false;
    }

    function objectValue() {
        offset += 1;
        whitespace();
        const keys = new Set();
        if (source[offset] === "}") {
            offset += 1;
            return false;
        }
        while (offset < source.length) {
            whitespace();
            const key = stringValue();
            if (keys.has(key)) return true;
            keys.add(key);
            whitespace();
            offset += 1; // colon; JSON.parse already established valid syntax.
            if (value()) return true;
            whitespace();
            if (source[offset] === "}") {
                offset += 1;
                return false;
            }
            offset += 1; // comma
        }
        return false;
    }

    function arrayValue() {
        offset += 1;
        whitespace();
        if (source[offset] === "]") {
            offset += 1;
            return false;
        }
        while (offset < source.length) {
            if (value()) return true;
            whitespace();
            if (source[offset] === "]") {
                offset += 1;
                return false;
            }
            offset += 1; // comma
        }
        return false;
    }

    return value();
}

/** Share only automatic reads; explicit Refresh always starts a fresh request. */
export function createAutomaticProfileLoader(
    fetchFresh,
    { cacheMilliseconds = 1000, clock = () => Date.now() } = {},
) {
    let automaticRequest = null;
    let cachedValue;
    let cachedAt = 0;
    let cachedSerial = 0;
    let hasCachedValue = false;
    let requestSerial = 0;

    async function fresh() {
        const serial = ++requestSerial;
        const result = await fetchFresh();
        if (serial >= cachedSerial) {
            cachedValue = result;
            cachedAt = clock();
            cachedSerial = serial;
            hasCachedValue = true;
        }
        return result;
    }

    return async function load({ automatic = false } = {}) {
        if (!automatic) return fresh();
        if (hasCachedValue && clock() - cachedAt <= cacheMilliseconds) {
            return cachedValue;
        }
        if (automaticRequest) return automaticRequest;
        const request = fresh();
        automaticRequest = request;
        try {
            return await request;
        } finally {
            if (automaticRequest === request) automaticRequest = null;
        }
    };
}

export function normalizeTaskProfile(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    if (Object.keys(value).sort().join("\0") !== PROFILE_KEYS.join("\0")) return null;
    if (value.schema_version !== 1) return null;
    const id = boundedText(value.id, 64, { allowEmpty: false });
    const name = boundedText(value.name, 128, { allowEmpty: false });
    const description = boundedText(value.description, 2048);
    const systemPrompt = boundedText(value.system_prompt, 65536);
    const prefix = boundedText(value.prompt_prefix, 65536);
    const suffix = boundedText(value.prompt_suffix, 65536);
    if (
        !id ||
        !/^[a-z0-9][a-z0-9._-]{0,63}$/.test(id) ||
        !name?.trim() ||
        description === null ||
        systemPrompt === null ||
        prefix === null ||
        suffix === null
    ) {
        return null;
    }
    return {
        description,
        id,
        name,
        prompt_prefix: prefix,
        prompt_suffix: suffix,
        schema_version: 1,
        system_prompt: systemPrompt,
    };
}

export function canonicalProfileJSON(value) {
    const profile = normalizeTaskProfile(value);
    return profile ? JSON.stringify(profile) : null;
}

export function parseProfileSnapshot(text) {
    if (typeof text !== "string" || new TextEncoder().encode(text).length > 262144) {
        return null;
    }
    try {
        const parsed = JSON.parse(text);
        return hasDuplicateObjectKeys(text) ? null : normalizeTaskProfile(parsed);
    } catch {
        return null;
    }
}

export function normalizeProfilesResponse(value) {
    if (!value || value.schema_version !== 1 || !Array.isArray(value.profiles)) return [];
    const profiles = [];
    const seen = new Set();
    for (const candidate of value.profiles.slice(0, 129)) {
        const profile = normalizeTaskProfile(candidate);
        if (!profile || seen.has(profile.id)) continue;
        seen.add(profile.id);
        profiles.push(profile);
    }
    return profiles;
}

export function reconcileProfileSelection(
    snapshotText,
    profiles,
    currentSelection,
    userChanged = false,
) {
    if (userChanged) return currentSelection;
    const snapshot = parseProfileSnapshot(snapshotText);
    return snapshot && profiles.some((profile) => profile.id === snapshot.id)
        ? snapshot.id
        : currentSelection;
}

export function profileSnapshotStatus(snapshotText, profiles) {
    const snapshot = parseProfileSnapshot(snapshotText);
    if (!snapshot) {
        return { kind: "invalid", text: "Saved snapshot is invalid" };
    }
    const local = profiles.find((profile) => profile.id === snapshot.id);
    if (!local) {
        return {
            kind: "missing",
            text: `Saved: ${snapshot.name} (local profile missing)`,
        };
    }
    const unchanged = canonicalProfileJSON(local) === canonicalProfileJSON(snapshot);
    return unchanged
        ? { kind: "current", text: `Saved: ${snapshot.name} (current)` }
        : { kind: "changed", text: `Saved: ${snapshot.name} (local profile changed)` };
}
