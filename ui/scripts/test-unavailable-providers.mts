// Pure-logic tests for the unavailable-provider handling used by
// ServiceConfigurationForm. Run: `npm run test:unavailable-providers` from ui/
// (Node 24+ strips TS types natively). Mirrors
// api/tests/test_provider_availability.py.

import assert from "node:assert/strict";

import {
    parseUnavailable,
    unavailableForService,
} from "../src/lib/unavailableProviders.ts";

let passed = 0;
function test(name: string, fn: () => void) {
    fn();
    passed += 1;
    console.log(`  ok  ${name}`);
}

const api = {
    llm: [
        { id: "azure", state: "unavailable", reason_code: "unsupported_by_voice_runtime", message: "Not available: runtime." },
        { id: "groq", state: "unavailable", reason_code: "unsupported_by_voice_runtime", message: "Not available: runtime." },
    ],
    tts: [{ id: "elevenlabs", state: "unavailable", reason_code: "unsupported_by_voice_runtime", message: "Not available: runtime." }],
    stt: [{ id: "assemblyai", state: "unavailable", reason_code: "unsupported_by_voice_runtime", message: "Not available: runtime." }],
    realtime: [],
};

test("keeps id, state, reason code and message for LLM, TTS and STT", () => {
    const out = parseUnavailable(api);
    for (const svc of ["llm", "tts", "stt"]) {
        assert.ok(out[svc].length > 0, svc);
        for (const p of out[svc]) {
            assert.ok(p.id && p.state === "unavailable" && p.reason_code && p.message, `${svc}:${p.id}`);
        }
    }
    assert.deepEqual(out.realtime, []);
});

test("garbage input never throws and yields nothing", () => {
    for (const bad of [null, undefined, 5, "x", [], { llm: "nope" }, { llm: [null, 3, {}] }]) {
        const out = parseUnavailable(bad);
        assert.deepEqual(Object.values(out).flat(), [], JSON.stringify(bad));
    }
});

test("entries without an id are dropped; missing message gets a safe default", () => {
    const out = parseUnavailable({ llm: [{ id: "", message: "x" }, { id: "p", state: "unavailable" }] });
    assert.equal(out.llm.length, 1);
    assert.equal(out.llm[0].message, "Not available in this deployment.");
});

test("a provider that is selectable is never listed as unavailable", () => {
    const out = parseUnavailable({ llm: [{ id: "openai", message: "stale" }, { id: "azure", message: "m" }] });
    const shown = unavailableForService(out, "llm", ["openai", "google", "deepseek"]);
    assert.deepEqual(shown.map((p) => p.id), ["azure"]);
});

test("supported providers are untouched when nothing is unavailable", () => {
    assert.deepEqual(unavailableForService({}, "tts", ["cartesia", "openai"]), []);
});

test("unknown service yields an empty list", () => {
    assert.deepEqual(unavailableForService(parseUnavailable(api), "embeddings", []), []);
});

console.log(`${passed} passed`);
