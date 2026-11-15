import "server-only";

/** Largest auth-form body read; a real one is under 300 bytes. */
export const MAX_BODY_BYTES = 4 * 1024;

export type ReadResult = { kind: "ok"; text: string } | { kind: "too_large" } | { kind: "unreadable" };

/** Reads the body, never holding more than `MAX_BODY_BYTES`: Content-Length is
 * only a hint a client can lie about, so the stream itself is bounded too. */
export async function readBoundedBody(request: Request): Promise<ReadResult> {
  const declared = Number(request.headers.get("content-length"));
  if (Number.isFinite(declared) && declared > MAX_BODY_BYTES) return { kind: "too_large" };
  if (!request.body) return { kind: "unreadable" };
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > MAX_BODY_BYTES) {
        await reader.cancel();
        return { kind: "too_large" };
      }
      chunks.push(value);
    }
  } catch {
    return { kind: "unreadable" };
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return { kind: "ok", text: new TextDecoder().decode(bytes) };
}
