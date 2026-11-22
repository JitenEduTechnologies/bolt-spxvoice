/**
 * Minimal text/event-stream reader.
 *
 * The dashboard stream sends NAMED events (`event: lead_updated`). `EventSource.onmessage` only receives
 * unnamed events and EventSource has no wildcard, so every named event was silently dropped and no page
 * ever refreshed live. Reading the stream directly delivers every event type, including ones added later.
 */

export interface SseFrame {
  id?: string;
  event?: string;
  data: string;
}

function parseFrame(raw: string): SseFrame | null {
  const frame: SseFrame = { data: "" };
  let hasData = false;
  for (const line of raw.split("\n")) {
    if (!line || line.startsWith(":")) continue; // blank or keep-alive comment
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    const value = colon === -1 ? "" : line.slice(colon + 1).replace(/^ /, "");
    if (field === "id") frame.id = value;
    else if (field === "event") frame.event = value;
    else if (field === "data") {
      frame.data += (hasData ? "\n" : "") + value;
      hasData = true;
    }
  }
  return hasData ? frame : null;
}

/** Incremental parser: feed arbitrary chunks, receive the complete frames they finish. */
export function createSseParser() {
  let buffer = "";
  return {
    push(chunk: string): SseFrame[] {
      buffer += chunk.replace(/\r\n?/g, "\n");
      const frames: SseFrame[] = [];
      let end = buffer.indexOf("\n\n");
      while (end !== -1) {
        const frame = parseFrame(buffer.slice(0, end));
        buffer = buffer.slice(end + 2);
        if (frame) frames.push(frame);
        end = buffer.indexOf("\n\n");
      }
      return frames;
    },
  };
}

/**
 * Open the stream and invoke `onFrame` for every event until it ends. Always settles by throwing
 * (refused, network error, or server closed) unless `signal` aborts, so callers can reconnect.
 */
export async function readSseStream(
  url: string,
  signal: AbortSignal,
  handlers: { onOpen?: () => void; onFrame: (frame: SseFrame) => void },
  idleTimeoutMs = 45_000,
): Promise<void> {
  const res = await fetch(url, { headers: { Accept: "text/event-stream" }, cache: "no-store", signal });
  if (!res.ok || !res.body) throw new Error(`event stream refused (HTTP ${res.status})`);
  handlers.onOpen?.();
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  const parser = createSseParser();
  try {
    for (;;) {
      const { done, value } = await readWithIdleTimeout(reader, idleTimeoutMs);
      if (done) throw new Error("event stream closed");
      for (const frame of parser.push(decoder.decode(value, { stream: true }))) handlers.onFrame(frame);
    }
  } finally {
    reader.cancel().catch(() => {});
  }
}

/**
 * The server sends a keep-alive comment every 15 s. A connection that goes silent for longer has died
 * without telling us (sleep/wake, Wi-Fi switch, proxy drop) - give up on it so the caller reconnects.
 */
function readWithIdleTimeout(reader: ReadableStreamDefaultReader<Uint8Array>, ms: number) {
  return new Promise<ReadableStreamReadResult<Uint8Array>>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("event stream went silent")), ms);
    reader.read().then(
      (r) => {
        clearTimeout(timer);
        resolve(r);
      },
      (e) => {
        clearTimeout(timer);
        reject(e);
      },
    );
  });
}
