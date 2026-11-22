// node ui/scripts/test-sse-stream.mts   (Node 22+, type stripping)
import assert from "node:assert/strict";
import { createSseParser } from "../src/lib/sseStream.ts";

let passed = 0;
const check = (name: string, fn: () => void) => {
  fn();
  passed += 1;
  console.log("ok -", name);
};

check("named events are delivered (what EventSource.onmessage drops)", () => {
  const p = createSseParser();
  const frames = p.push('id: 37\nevent: call_completed\ndata: {"event_type":"call_completed","id":37}\n\n');
  assert.equal(frames.length, 1);
  assert.equal(frames[0].event, "call_completed");
  assert.equal(frames[0].id, "37");
  assert.equal(JSON.parse(frames[0].data).event_type, "call_completed");
});

check("keep-alive comments and the connected preamble produce no frames", () => {
  const p = createSseParser();
  assert.deepEqual(p.push(": connected\n\n: keep-alive\n\n"), []);
});

check("frames split across arbitrary chunk boundaries are reassembled", () => {
  const p = createSseParser();
  const wire = 'id: 1\nevent: lead_created\ndata: {"a":1}\n\nid: 2\nevent: lead_updated\ndata: {"a":2}\n\n';
  const out = [];
  for (let i = 0; i < wire.length; i += 7) out.push(...p.push(wire.slice(i, i + 7)));
  assert.deepEqual(out.map((f) => f.event), ["lead_created", "lead_updated"]);
  assert.deepEqual(out.map((f) => JSON.parse(f.data).a), [1, 2]);
});

check("CRLF line endings and multi-line data are handled", () => {
  const p = createSseParser();
  const [f] = p.push("event: x\r\ndata: line1\r\ndata: line2\r\n\r\n");
  assert.equal(f.data, "line1\nline2");
});

check("an incomplete frame is held until its terminator arrives", () => {
  const p = createSseParser();
  assert.deepEqual(p.push("event: x\ndata: {}"), []);
  assert.equal(p.push("\n\n").length, 1);
});

check("unnamed messages still work", () => {
  const p = createSseParser();
  const [f] = p.push('data: {"event_type":"y"}\n\n');
  assert.equal(f.event, undefined);
  assert.equal(JSON.parse(f.data).event_type, "y");
});

// --- readSseStream: delivery, closure and the silent-connection watchdog (fetch is faked) ---
import { readSseStream } from "../src/lib/sseStream.ts";

const realFetch = globalThis.fetch;
const fakeStream = (chunks: string[], stayOpen: boolean) =>
  (async () =>
    new Response(
      new ReadableStream<Uint8Array>({
        start(controller) {
          const enc = new TextEncoder();
          for (const c of chunks) controller.enqueue(enc.encode(c));
          if (!stayOpen) controller.close();
        },
      }),
      { status: 200, headers: { "content-type": "text/event-stream" } },
    )) as typeof fetch;

{
  globalThis.fetch = fakeStream(['id: 5\nevent: lead_updated\ndata: {"event_type":"lead_updated"}\n\n'], false);
  const seen: string[] = [];
  await assert.rejects(
    readSseStream("/x", new AbortController().signal, { onFrame: (f) => seen.push(f.event ?? "") }),
    /closed/,
  );
  assert.deepEqual(seen, ["lead_updated"]);
  passed += 1;
  console.log("ok - frames are delivered, then a closed stream rejects so the caller reconnects");
}
{
  globalThis.fetch = fakeStream([": connected\n\n"], true);
  const started = Date.now();
  await assert.rejects(readSseStream("/x", new AbortController().signal, { onFrame: () => {} }, 60), /silent/);
  assert.ok(Date.now() - started < 2000);
  passed += 1;
  console.log("ok - a connection that goes silent is abandoned (sleep/wake, dropped proxy)");
}
{
  globalThis.fetch = (async () => new Response("nope", { status: 401 })) as typeof fetch;
  await assert.rejects(readSseStream("/x", new AbortController().signal, { onFrame: () => {} }), /HTTP 401/);
  passed += 1;
  console.log("ok - a refused stream (expired ticket) rejects so the caller fetches a new ticket");
}
globalThis.fetch = realFetch;

console.log(`${passed} checks passed`);
