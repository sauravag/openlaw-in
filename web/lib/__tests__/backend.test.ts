// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";

type FetchMock = ReturnType<typeof vi.fn>;

function stubBackend(response: Response): FetchMock {
  const mock = vi.fn(async () => response);
  vi.stubGlobal("fetch", mock);
  return mock;
}

async function load(authEnabled: boolean) {
  vi.resetModules();
  vi.stubEnv("NEXT_PUBLIC_OPENLAW_AUTH_ENABLED", authEnabled ? "true" : "false");
  vi.stubEnv("OPENLAW_API_URL", "http://backend.test");
  return import("../backend");
}

function sentHeaders(mock: FetchMock): Headers {
  const init = mock.mock.calls[0][1] as RequestInit;
  return new Headers(init.headers);
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("proxyToBackend", () => {
  it("forwards to OPENLAW_API_URL plus the backend path and query string", async () => {
    const mock = stubBackend(new Response("{}"));
    const { proxyToBackend } = await load(false);
    await proxyToBackend(new Request("http://web/api/openlaw/x?a=1"), "/conversations/abc");
    expect(mock.mock.calls[0][0]).toBe("http://backend.test/conversations/abc?a=1");
  });

  it("forwards the inbound Cookie header verbatim", async () => {
    const mock = stubBackend(new Response("{}"));
    const { proxyToBackend } = await load(false);
    await proxyToBackend(
      new Request("http://web/x", { headers: { cookie: "openlaw_anon_id=abc; other=1" } }),
      "/ask",
    );
    expect(sentHeaders(mock).get("cookie")).toBe("openlaw_anon_id=abc; other=1");
  });

  it("appends every backend Set-Cookie separately", async () => {
    const headers = new Headers();
    headers.append("set-cookie", "openlaw_anon_id=abc; Path=/; HttpOnly");
    headers.append("set-cookie", "second=2; Path=/");
    stubBackend(new Response("{}", { headers }));
    const { proxyToBackend } = await load(false);
    const response = await proxyToBackend(new Request("http://web/x"), "/ask");
    expect(response.headers.getSetCookie()).toEqual([
      "openlaw_anon_id=abc; Path=/; HttpOnly",
      "second=2; Path=/",
    ]);
  });

  it("preserves status, Retry-After and body of a 429", async () => {
    stubBackend(
      new Response('{"detail":"quota"}', { status: 429, headers: { "retry-after": "42" } }),
    );
    const { proxyToBackend } = await load(false);
    const response = await proxyToBackend(new Request("http://web/x"), "/ask");
    expect(response.status).toBe(429);
    expect(response.headers.get("retry-after")).toBe("42");
    expect(await response.json()).toEqual({ detail: "quota" });
  });

  it("returns a 204 without a body", async () => {
    stubBackend(new Response(null, { status: 204 }));
    const { proxyToBackend } = await load(false);
    const response = await proxyToBackend(
      new Request("http://web/x", { method: "DELETE" }),
      "/documents/1",
    );
    expect(response.status).toBe(204);
  });

  it("streams a multipart body through unmodified with duplex half", async () => {
    const mock = stubBackend(new Response("{}", { status: 201 }));
    const { proxyToBackend } = await load(false);
    const form = new FormData();
    form.append("file", new Blob(["hello"], { type: "text/plain" }), "a.txt");
    const request = new Request("http://web/x", { method: "POST", body: form });
    const contentType = request.headers.get("content-type");
    await proxyToBackend(request, "/documents");
    const init = mock.mock.calls[0][1] as RequestInit & { duplex?: string };
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(ReadableStream);
    expect(init.duplex).toBe("half");
    expect(sentHeaders(mock).get("content-type")).toBe(contentType);
  });

  it("sends no body on GET", async () => {
    const mock = stubBackend(new Response("{}"));
    const { proxyToBackend } = await load(false);
    await proxyToBackend(new Request("http://web/x"), "/conversations/1");
    const init = mock.mock.calls[0][1] as RequestInit;
    expect(init.body ?? null).toBeNull();
  });

  it("returns 502 with a plain message when the backend is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("fetch failed"); }));
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const { proxyToBackend } = await load(false);
    const response = await proxyToBackend(new Request("http://web/x"), "/ask");
    expect(response.status).toBe(502);
    errorSpy.mockRestore();
  });
});

describe("proxyToBackend Authorization", () => {
  const withSession = { cookie: "openlaw_anon_id=a; openlaw_session=tok123" };

  it("attaches Bearer when AUTH_ENABLED and the session cookie is present", async () => {
    const mock = stubBackend(new Response("{}"));
    const { proxyToBackend } = await load(true);
    await proxyToBackend(new Request("http://web/x", { headers: withSession }), "/ask");
    expect(sentHeaders(mock).get("authorization")).toBe("Bearer tok123");
  });

  it("attaches nothing when AUTH_ENABLED but no session cookie", async () => {
    const mock = stubBackend(new Response("{}"));
    const { proxyToBackend } = await load(true);
    await proxyToBackend(
      new Request("http://web/x", { headers: { cookie: "openlaw_anon_id=a" } }),
      "/ask",
    );
    expect(sentHeaders(mock).get("authorization")).toBeNull();
  });

  it("attaches nothing when the session cookie is present but auth is disabled", async () => {
    const mock = stubBackend(new Response("{}"));
    const { proxyToBackend } = await load(false);
    await proxyToBackend(new Request("http://web/x", { headers: withSession }), "/ask");
    expect(sentHeaders(mock).get("authorization")).toBeNull();
  });

  it("never forwards a client-supplied Authorization header (either mode)", async () => {
    for (const mode of [false, true]) {
      const mock = stubBackend(new Response("{}"));
      const { proxyToBackend } = await load(mode);
      await proxyToBackend(
        new Request("http://web/x", {
          headers: { authorization: "Bearer attacker", cookie: "openlaw_anon_id=a" },
        }),
        "/ask",
      );
      expect(sentHeaders(mock).get("authorization")).toBeNull();
    }
  });

  it("replaces a client-supplied Authorization with the session token", async () => {
    const mock = stubBackend(new Response("{}"));
    const { proxyToBackend } = await load(true);
    await proxyToBackend(
      new Request("http://web/x", {
        headers: { authorization: "Bearer attacker", ...withSession },
      }),
      "/ask",
    );
    expect(sentHeaders(mock).get("authorization")).toBe("Bearer tok123");
  });
});
