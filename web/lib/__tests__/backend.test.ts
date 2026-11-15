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
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("fetch failed http://backend.test"); }));
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const { proxyToBackend } = await load(false);
    const response = await proxyToBackend(new Request("http://web/x"), "/ask");
    expect(response.status).toBe(502);
    const text = await response.text();
    expect(JSON.parse(text)).toEqual({ detail: "The service is unavailable." });
    expect(text).not.toContain("fetch failed");
    expect(text).not.toContain("backend.test");
    errorSpy.mockRestore();
  });
});

describe("proxyToBackend Authorization", () => {
  const FUTURE = Math.floor(Date.now() / 1000) + 3600;
  const session = (over: Record<string, unknown> = {}) =>
    encodeURIComponent(
      JSON.stringify({
        accessToken: "tok123",
        refreshToken: "refresh456",
        expiresAt: FUTURE,
        email: "a@example.com",
        ...over,
      }),
    );
  const withCookie = (value: string) => ({ cookie: `openlaw_anon_id=a; openlaw_session=${value}` });
  const withSession = withCookie(session());

  async function authorizationFor(headers: Record<string, string>, authEnabled = true) {
    const mock = stubBackend(new Response("{}"));
    const { proxyToBackend } = await load(authEnabled);
    await proxyToBackend(new Request("http://web/x", { headers }), "/ask");
    return sentHeaders(mock).get("authorization");
  }

  it("attaches the access token (not the cookie JSON) as Bearer", async () => {
    expect(await authorizationFor(withSession)).toBe("Bearer tok123");
  });

  it("never sends the refresh token to the backend", async () => {
    expect(await authorizationFor(withSession)).not.toContain("refresh456");
  });

  it("attaches nothing when AUTH_ENABLED but no session cookie", async () => {
    expect(await authorizationFor({ cookie: "openlaw_anon_id=a" })).toBeNull();
  });

  it("attaches nothing for an expired session", async () => {
    expect(await authorizationFor(withCookie(session({ expiresAt: 1 })))).toBeNull();
  });

  it("attaches nothing within the 60s expiry skew", async () => {
    const soon = Math.floor(Date.now() / 1000) + 30;
    expect(await authorizationFor(withCookie(session({ expiresAt: soon })))).toBeNull();
  });

  it("attaches nothing for malformed JSON", async () => {
    expect(await authorizationFor(withCookie(encodeURIComponent("{oops")))).toBeNull();
  });

  it("attaches nothing for a raw garbage cookie value", async () => {
    expect(await authorizationFor(withCookie("tok123"))).toBeNull();
  });

  it("attaches nothing when the session cookie appears twice", async () => {
    const twice = { cookie: `openlaw_session=${session()}; openlaw_session=${session({ accessToken: "evil" })}` };
    expect(await authorizationFor(twice)).toBeNull();
  });

  it("attaches nothing when the session cookie is present but auth is disabled", async () => {
    expect(await authorizationFor(withSession, false)).toBeNull();
  });

  it("never forwards a client-supplied Authorization header (either mode)", async () => {
    for (const mode of [false, true]) {
      expect(
        await authorizationFor(
          { authorization: "Bearer attacker", cookie: "openlaw_anon_id=a" },
          mode,
        ),
      ).toBeNull();
    }
  });

  it("replaces a client-supplied Authorization with the session token", async () => {
    expect(await authorizationFor({ authorization: "Bearer attacker", ...withSession })).toBe(
      "Bearer tok123",
    );
  });
});

describe("proxyToBackend hop-by-hop headers (I1)", () => {
  it("does not forward the RFC 9110 hop-by-hop set, and forwards the request", async () => {
    const mock = stubBackend(new Response("{}"));
    const { proxyToBackend } = await load(false);
    const request = new Request("http://web/x", {
      method: "POST",
      body: "{}",
      headers: {
        "content-type": "application/json",
        "transfer-encoding": "chunked",
        "keep-alive": "timeout=5",
        "proxy-connection": "keep-alive",
        te: "trailers",
        trailer: "x",
        upgrade: "h2c",
        expect: "100-continue",
        connection: "keep-alive, x-private",
        "x-private": "secret",
        "x-keep": "1",
      },
    });
    const response = await proxyToBackend(request, "/ask");
    expect(response.status).toBe(200);
    const sent = sentHeaders(mock);
    for (const name of [
      "transfer-encoding", "keep-alive", "proxy-connection", "te", "trailer",
      "upgrade", "expect", "connection", "x-private", "host",
    ]) {
      expect(sent.has(name), name).toBe(false);
    }
    expect(sent.get("x-keep")).toBe("1");
    expect(sent.get("content-type")).toBe("application/json");
  });
});

describe("proxyToBackend CSRF guard", () => {
  async function post(headers: Record<string, string>, method = "POST") {
    const mock = stubBackend(new Response("{}"));
    const { proxyToBackend } = await load(false);
    const response = await proxyToBackend(
      new Request("http://web.test/api/openlaw/ask", {
        method,
        body: method === "GET" ? undefined : "{}",
        headers,
      }),
      "/ask",
    );
    return { response, mock };
  }

  it("rejects a cross-site POST with a generic 403 and never calls the backend", async () => {
    const { response, mock } = await post({ "sec-fetch-site": "cross-site", origin: "http://evil.test" });
    expect(response.status).toBe(403);
    expect(await response.json()).toEqual({ detail: "Forbidden." });
    expect(mock).not.toHaveBeenCalled();
  });

  it("rejects a cross-site DELETE and a foreign Origin without Sec-Fetch-Site", async () => {
    expect((await post({ "sec-fetch-site": "same-site" }, "DELETE")).response.status).toBe(403);
    expect((await post({ origin: "http://evil.test" })).response.status).toBe(403);
    expect((await post({ origin: "null" })).response.status).toBe(403);
  });

  it("allows a same-origin or user-initiated (none) POST", async () => {
    expect((await post({ "sec-fetch-site": "same-origin" })).response.status).toBe(200);
    expect((await post({ "sec-fetch-site": "none" })).response.status).toBe(200);
  });

  it("allows a POST whose Origin host equals the request host", async () => {
    expect((await post({ origin: "http://web.test", host: "web.test" })).response.status).toBe(200);
  });

  it("uses X-Forwarded-Host, when present, for the Origin comparison", async () => {
    const ok = await post({ origin: "https://openlaw.in", host: "web:3000", "x-forwarded-host": "openlaw.in" });
    expect(ok.response.status).toBe(200);
    const bad = await post({ origin: "https://web:3000", host: "web:3000", "x-forwarded-host": "openlaw.in" });
    expect(bad.response.status).toBe(403);
  });

  // Non-browser clients (curl) send neither header and CSRF needs a browser,
  // which always sends Sec-Fetch-Site or Origin on cross-site POST/DELETE.
  it("allows a POST with neither Sec-Fetch-Site nor Origin", async () => {
    expect((await post({})).response.status).toBe(200);
  });

  it("allows a cross-site GET (safe method)", async () => {
    expect((await post({ "sec-fetch-site": "cross-site" }, "GET")).response.status).toBe(200);
  });
});

describe("proxyToBackend path, signal and redirect hardening", () => {
  it.each([".", "..", "%2e%2e", "%252E%252E"])("rejects a %s dynamic segment with 400", async (segment) => {
    const mock = stubBackend(new Response("{}"));
    const { proxyToBackend } = await load(false);
    const response = await proxyToBackend(
      new Request("http://web/x"),
      `/conversations/${encodeURIComponent(segment)}`,
    );
    expect(response.status).toBe(400);
    expect(mock).not.toHaveBeenCalled();
  });

  it("forwards request.signal to fetch", async () => {
    const mock = stubBackend(new Response("{}"));
    const { proxyToBackend } = await load(false);
    const controller = new AbortController();
    await proxyToBackend(new Request("http://web/x", { signal: controller.signal }), "/ask");
    const init = mock.mock.calls[0][1] as RequestInit;
    expect(init.signal).toBeInstanceOf(AbortSignal);
    controller.abort();
    expect(init.signal?.aborted).toBe(true);
  });

  it("drops a Location header pointing at the backend origin", async () => {
    stubBackend(new Response(null, { status: 307, headers: { location: "http://backend.test/ask/" } }));
    const { proxyToBackend } = await load(false);
    const response = await proxyToBackend(new Request("http://web/x"), "/ask");
    expect(response.headers.has("location")).toBe(false);
  });

  it("keeps a Location header for another origin", async () => {
    stubBackend(new Response(null, { status: 307, headers: { location: "https://other.test/y" } }));
    const { proxyToBackend } = await load(false);
    const response = await proxyToBackend(new Request("http://web/x"), "/ask");
    expect(response.headers.get("location")).toBe("https://other.test/y");
  });
});
