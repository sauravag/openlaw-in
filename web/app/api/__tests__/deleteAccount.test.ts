// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

interface SetCall {
  readonly name: string;
  readonly value: string;
  readonly options: Record<string, unknown>;
}

const sets: SetCall[] = [];
let cookieHeader = "";

vi.mock("next/headers", () => ({
  cookies: async () => ({
    set: (name: string, value: string, options: Record<string, unknown>) => {
      sets.push({ name, value, options });
    },
  }),
  headers: async () => new Headers(cookieHeader ? { cookie: cookieHeader } : {}),
}));

const revokeSession = vi.fn();
vi.mock("@/lib/auth/gotrue", () => ({ revokeSession }));

const backendFetch = vi.fn<typeof fetch>();
const NOW_SECONDS = Math.floor(Date.now() / 1000);
const SAME_ORIGIN = { "sec-fetch-site": "same-origin" };
const SIGNED_IN = `openlaw_session=${encodeURIComponent(
  JSON.stringify({
    accessToken: "access-token-secret",
    refreshToken: "refresh-token-secret",
    expiresAt: NOW_SECONDS + 3600,
    email: "person@example.com",
  }),
)}`;
const BACKEND_OK = {
  conversations_deleted: 1,
  documents_deleted: 2,
  messages_deleted: 4,
  audit_rows_retained: 4,
};

type Handler = (request: Request) => Promise<Response>;

async function load(authEnabled: boolean): Promise<Handler> {
  vi.resetModules();
  vi.stubEnv("NEXT_PUBLIC_OPENLAW_AUTH_ENABLED", authEnabled ? "true" : "false");
  vi.stubEnv("OPENLAW_API_URL", "http://backend.test");
  vi.stubEnv("OPENLAW_SUPABASE_URL", "https://project.supabase.test");
  vi.stubEnv("OPENLAW_SUPABASE_ANON_KEY", "anon-key");
  return (await import("../auth/delete-account/route")).POST;
}

function post(body: unknown, headers: Record<string, string> = SAME_ORIGIN): Request {
  return new Request("http://localhost:3000/api/auth/delete-account", {
    method: "POST",
    headers: { "content-type": "application/json", ...headers },
    body: JSON.stringify(body),
  });
}

const sessionCleared = (): boolean =>
  sets.some((c) => c.name === "openlaw_session" && c.options.maxAge === 0);

beforeEach(() => {
  sets.length = 0;
  cookieHeader = SIGNED_IN;
  revokeSession.mockReset().mockResolvedValue(undefined);
  backendFetch
    .mockReset()
    .mockImplementation(async () =>
      Response.json({ ...BACKEND_OK, identity_removed: true }),
    );
  vi.stubGlobal("fetch", backendFetch);
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("POST /api/auth/delete-account", () => {
  it("returns 404 in Mode A and calls nothing", async () => {
    const handler = await load(false);

    const response = await handler(post({ confirmation: "DELETE" }));

    expect(response.status).toBe(404);
    expect(backendFetch).not.toHaveBeenCalled();
    expect(sets).toHaveLength(0);
  });

  it("returns 403 for a cross-site POST and calls nothing", async () => {
    const handler = await load(true);

    const response = await handler(
      post({ confirmation: "DELETE" }, { "sec-fetch-site": "cross-site" }),
    );

    expect(response.status).toBe(403);
    expect(backendFetch).not.toHaveBeenCalled();
    expect(sets).toHaveLength(0);
  });

  it.each([
    ["missing", {}],
    ["empty", { confirmation: "" }],
    ["wrong word", { confirmation: "yes" }],
    ["not a string", { confirmation: true }],
    ["hindi word under english", { confirmation: "हटाएं", language: "en" }],
    ["english word under hindi", { confirmation: "DELETE", language: "hi" }],
  ])("rejects a %s confirmation with 400 and helplines, no backend call", async (_n, body) => {
    const handler = await load(true);

    const response = await handler(post(body));

    expect(response.status).toBe(400);
    const json = await response.json();
    expect(json.escalation_footer).toBeTruthy();
    expect(json.helplines.length).toBeGreaterThan(0);
    expect(backendFetch).not.toHaveBeenCalled();
    expect(sessionCleared()).toBe(false);
  });

  it("rejects an unparseable body with 400", async () => {
    const handler = await load(true);
    const request = new Request("http://localhost:3000/api/auth/delete-account", {
      method: "POST",
      headers: { "content-type": "application/json", ...SAME_ORIGIN },
      body: "{not json",
    });

    expect((await handler(request)).status).toBe(400);
    expect(backendFetch).not.toHaveBeenCalled();
  });

  it("caps the body at 4 KiB", async () => {
    const handler = await load(true);

    const response = await handler(post({ confirmation: "DELETE", pad: "x".repeat(5000) }));

    expect(response.status).toBe(413);
    expect(backendFetch).not.toHaveBeenCalled();
  });

  it("returns the 401 refusal and calls nothing when there is no session", async () => {
    cookieHeader = "";
    const handler = await load(true);

    const response = await handler(post({ confirmation: "DELETE" }));

    expect(response.status).toBe(401);
    expect((await response.json()).helplines.length).toBeGreaterThan(0);
    expect(backendFetch).not.toHaveBeenCalled();
  });

  it("deletes with the session's bearer, clears the session and redirects (JSON)", async () => {
    const handler = await load(true);

    const response = await handler(post({ confirmation: "delete ", language: "en" }));

    expect(backendFetch).toHaveBeenCalledTimes(1);
    const [url, init] = backendFetch.mock.calls[0];
    expect(String(url)).toBe("http://backend.test/account");
    expect(init?.method).toBe("DELETE");
    expect(new Headers(init?.headers).get("authorization")).toBe("Bearer access-token-secret");
    expect(new Headers(init?.headers).get("cookie")).toBeNull();
    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe("/?account=deleted&identity=removed");
    expect(sessionCleared()).toBe(true);
  });

  it("accepts the Hindi word in a form-urlencoded body", async () => {
    const handler = await load(true);
    const request = new Request("http://localhost:3000/api/auth/delete-account", {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded", ...SAME_ORIGIN },
      body: new URLSearchParams({ confirmation: "हटाएं", language: "hi" }),
    });

    const response = await handler(request);

    expect(response.status).toBe(303);
    expect(backendFetch).toHaveBeenCalledTimes(1);
  });

  it("flags identity=remains when the backend did not remove the identity", async () => {
    backendFetch.mockImplementation(async () =>
      Response.json({ ...BACKEND_OK, identity_removed: false }),
    );
    const handler = await load(true);

    const response = await handler(post({ confirmation: "DELETE" }));

    expect(response.headers.get("location")).toBe("/?account=deleted&identity=remains");
    expect(sessionCleared()).toBe(true);
  });

  it("never puts counts in the redirect URL", async () => {
    const handler = await load(true);

    const response = await handler(post({ confirmation: "DELETE" }));

    expect(response.headers.get("location")).not.toMatch(/\d/);
  });

  it.each([500, 503])("keeps the session when the backend answers %s", async (status) => {
    backendFetch.mockImplementation(async () => new Response("{}", { status }));
    const handler = await load(true);

    const response = await handler(post({ confirmation: "DELETE" }));

    expect(response.status).toBe(502);
    expect((await response.json()).helplines.length).toBeGreaterThan(0);
    expect(sets).toHaveLength(0);
  });

  it("keeps the session when the backend is unreachable", async () => {
    backendFetch.mockRejectedValue(new Error("network down"));
    const handler = await load(true);

    const response = await handler(post({ confirmation: "DELETE" }));

    expect(response.status).toBe(502);
    expect(sets).toHaveLength(0);
  });

  it("returns the sign-in refusal, keeping the session, when the backend says 401", async () => {
    backendFetch.mockImplementation(async () => new Response("{}", { status: 401 }));
    const handler = await load(true);

    const response = await handler(post({ confirmation: "DELETE" }));

    expect(response.status).toBe(401);
    expect(sets).toHaveLength(0);
  });

  it("accepts the chandrabindu spelling of the Hindi word", async () => {
    const handler = await load(true);

    const response = await handler(post({ confirmation: "हटाएँ", language: "hi" }));

    expect(response.status).toBe(303);
    expect(backendFetch).toHaveBeenCalledTimes(1);
  });

  it("revokes the session at GoTrue before clearing it, on success", async () => {
    const handler = await load(true);

    await handler(post({ confirmation: "DELETE" }));

    expect(revokeSession).toHaveBeenCalledTimes(1);
    expect(revokeSession.mock.calls[0][1]).toBe("access-token-secret");
    expect(sessionCleared()).toBe(true);
  });

  it("still clears the session and redirects when revokeSession throws", async () => {
    revokeSession.mockRejectedValue(new Error("gotrue down"));
    const handler = await load(true);

    const response = await handler(post({ confirmation: "DELETE" }));

    expect(response.status).toBe(303);
    expect(sessionCleared()).toBe(true);
  });

  it("does not revoke when the deletion failed", async () => {
    backendFetch.mockImplementation(async () => new Response("{}", { status: 500 }));
    const handler = await load(true);

    await handler(post({ confirmation: "DELETE" }));

    expect(revokeSession).not.toHaveBeenCalled();
  });

  it("uses delete-specific 413 copy, not the sign-in email copy", async () => {
    const handler = await load(true);

    const response = await handler(post({ confirmation: "DELETE", pad: "x".repeat(5000) }));

    const { detail } = await response.json();
    expect(detail).toMatch(/nothing was deleted/);
    expect(detail).not.toMatch(/email/i);
  });

  it("maps a backend 409 to a non-retry refusal and keeps the session", async () => {
    backendFetch.mockImplementation(async () => new Response("{}", { status: 409 }));
    const handler = await load(true);

    const response = await handler(post({ confirmation: "DELETE" }));

    expect(response.status).toBe(409);
    const { detail } = await response.json();
    expect(detail).toMatch(/will not help/);
    expect(detail).not.toMatch(/try again in a little while/i);
    expect(sets).toHaveLength(0);
  });
});
