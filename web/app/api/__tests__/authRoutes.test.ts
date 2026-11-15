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

const requestOtp = vi.fn();
const exchangeCode = vi.fn();
const revokeSession = vi.fn();
vi.mock("@/lib/auth/gotrue", () => ({ requestOtp, exchangeCode, revokeSession }));

const checkAuthAgreement = vi.fn();
vi.mock("@/lib/authAgreement", () => ({
  checkAuthAgreement,
  authUnavailableResponse: async () =>
    Response.json({ detail: "unavailable-marker" }, { status: 503 }),
}));

const backendFetch = vi.fn<typeof fetch>();

const NOW_SECONDS = Math.floor(Date.now() / 1000);
const SESSION = {
  kind: "session" as const,
  accessToken: "access-token-secret",
  refreshToken: "refresh-token-secret",
  expiresAt: NOW_SECONDS + 3600,
  email: "person@example.com",
};
const SAME_ORIGIN = { "sec-fetch-site": "same-origin" };

type Handler = (request: Request) => Promise<Response>;
interface Routes {
  signIn: Handler;
  callback: Handler;
  signOut: Handler;
  session: Handler;
}

async function load(authEnabled: boolean): Promise<Routes> {
  vi.resetModules();
  vi.stubEnv("NEXT_PUBLIC_OPENLAW_AUTH_ENABLED", authEnabled ? "true" : "false");
  vi.stubEnv("OPENLAW_SUPABASE_URL", "https://project.supabase.test");
  vi.stubEnv("OPENLAW_SUPABASE_ANON_KEY", "anon-key");
  vi.stubEnv("OPENLAW_SITE_URL", "https://openlaw.example");
  const [signIn, callback, signOut, session] = await Promise.all([
    import("../auth/sign-in/route"),
    import("../auth/callback/route"),
    import("../auth/sign-out/route"),
    import("../auth/session/route"),
  ]);
  return {
    signIn: signIn.POST,
    callback: callback.GET,
    signOut: signOut.POST,
    session: session.GET,
  };
}

function jsonPost(body: unknown, headers: Record<string, string> = SAME_ORIGIN): Request {
  return new Request("http://localhost:3000/api/auth/sign-in", {
    method: "POST",
    headers: { "content-type": "application/json", ...headers },
    body: JSON.stringify(body),
  });
}

function expectHelp(body: Record<string, unknown>): void {
  expect(typeof body.escalation_footer).toBe("string");
  expect((body.escalation_footer as string).length).toBeGreaterThan(0);
  expect(Array.isArray(body.helplines)).toBe(true);
  expect((body.helplines as unknown[]).length).toBeGreaterThan(0);
}

beforeEach(() => {
  sets.length = 0;
  cookieHeader = "";
  requestOtp.mockReset().mockResolvedValue({ kind: "sent" });
  exchangeCode.mockReset().mockResolvedValue(SESSION);
  revokeSession.mockReset().mockResolvedValue(undefined);
  checkAuthAgreement.mockReset().mockResolvedValue({ agrees: true });
  backendFetch
    .mockReset()
    .mockImplementation(async () => Response.json({ conversations_moved: 0, documents_moved: 0 }));
  vi.stubGlobal("fetch", backendFetch);
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("Mode A (auth disabled)", () => {
  it("returns 404 from all four routes and calls nothing", async () => {
    const routes = await load(false);

    const responses = await Promise.all([
      routes.signIn(jsonPost({ email: "a@b.co" })),
      routes.callback(new Request("http://localhost:3000/api/auth/callback?code=x")),
      routes.signOut(jsonPost({})),
      routes.session(new Request("http://localhost:3000/api/auth/session")),
    ]);

    expect(responses.map((r) => r.status)).toEqual([404, 404, 404, 404]);
    expect(requestOtp).not.toHaveBeenCalled();
    expect(exchangeCode).not.toHaveBeenCalled();
    expect(revokeSession).not.toHaveBeenCalled();
    expect(checkAuthAgreement).not.toHaveBeenCalled();
    expect(sets).toHaveLength(0);
  });
});

describe("POST /api/auth/sign-in", () => {
  it("returns 202 with an HttpOnly verifier cookie and calls requestOtp once", async () => {
    const { signIn } = await load(true);

    const response = await signIn(jsonPost({ email: "Person@Example.com", language: "en" }));

    expect(response.status).toBe(202);
    expect(requestOtp).toHaveBeenCalledTimes(1);
    const otp = requestOtp.mock.calls[0][1];
    expect(otp.identifier).toBe("person@example.com");
    expect(otp.method).toBe("email");
    expect(otp.redirectTo).toBe("https://openlaw.example/api/auth/callback");
    expect(typeof otp.codeChallenge).toBe("string");
    const verifier = sets.find((c) => c.name === "openlaw_pkce");
    expect(verifier?.options.httpOnly).toBe(true);
    expect(verifier?.value).not.toBe(otp.codeChallenge);
  });

  it("accepts a form-urlencoded body from the no-JavaScript form", async () => {
    const { signIn } = await load(true);
    const request = new Request("http://localhost:3000/api/auth/sign-in", {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded", ...SAME_ORIGIN },
      body: new URLSearchParams({ email: "form@example.com", language: "hi" }),
    });

    const response = await signIn(request);

    expect(response.status).toBe(303);
    expect(requestOtp.mock.calls[0][1].identifier).toBe("form@example.com");
  });

  it("rejects a malformed email with 400 and helplines before requestOtp", async () => {
    const { signIn } = await load(true);

    for (const email of ["", "nope", "a@b", "a b@c.co", 42, null]) {
      const response = await signIn(jsonPost({ email }));
      expect(response.status).toBe(400);
      expectHelp(await response.json());
    }
    expect(requestOtp).not.toHaveBeenCalled();
  });

  it("rejects unparseable bodies with 400", async () => {
    const { signIn } = await load(true);
    const request = new Request("http://localhost:3000/api/auth/sign-in", {
      method: "POST",
      headers: { "content-type": "application/json", ...SAME_ORIGIN },
      body: "{not json",
    });

    const response = await signIn(request);

    expect(response.status).toBe(400);
    expect(requestOtp).not.toHaveBeenCalled();
  });

  it("returns 403 for a cross-site POST and never calls requestOtp", async () => {
    const { signIn } = await load(true);

    const response = await signIn(
      jsonPost({ email: "victim@example.com" }, { "sec-fetch-site": "cross-site" }),
    );

    expect(response.status).toBe(403);
    expect(requestOtp).not.toHaveBeenCalled();
    expect(sets).toHaveLength(0);
  });

  it("returns 403 for a foreign Origin header", async () => {
    const { signIn } = await load(true);

    const response = await signIn(
      jsonPost({ email: "victim@example.com" }, { origin: "http://evil.test" }),
    );

    expect(response.status).toBe(403);
    expect(requestOtp).not.toHaveBeenCalled();
  });

  it("returns 429 with Retry-After and helplines when GoTrue rate limits", async () => {
    requestOtp.mockResolvedValue({ kind: "rate_limited", retryAfterSeconds: 42 });
    const { signIn } = await load(true);

    const response = await signIn(jsonPost({ email: "a@example.com" }));

    expect(response.status).toBe(429);
    expect(response.headers.get("retry-after")).toBe("42");
    expectHelp(await response.json());
  });

  it("returns 503 with helplines when GoTrue fails", async () => {
    requestOtp.mockResolvedValue({ kind: "failed", detail: "gotrue_http_500" });
    const { signIn } = await load(true);

    const response = await signIn(jsonPost({ email: "a@example.com" }));

    const body = await response.json();
    expect(response.status).toBe(503);
    expectHelp(body);
    expect(JSON.stringify(body)).not.toContain("gotrue_http_500");
  });

  it("returns 503 with helplines when the GoTrue config is missing", async () => {
    const { signIn } = await load(true);
    vi.stubEnv("OPENLAW_SUPABASE_URL", "");

    const response = await signIn(jsonPost({ email: "a@example.com" }));

    expect(response.status).toBe(503);
    expectHelp(await response.json());
    expect(requestOtp).not.toHaveBeenCalled();
  });

  it("returns 503 with helplines when requestOtp throws", async () => {
    requestOtp.mockRejectedValue(new Error("boom"));
    const { signIn } = await load(true);

    const response = await signIn(jsonPost({ email: "a@example.com" }));

    expect(response.status).toBe(503);
    expectHelp(await response.json());
  });

  it("returns an identical 202 body whatever the address", async () => {
    const { signIn } = await load(true);

    const first = await signIn(jsonPost({ email: "registered@example.com" }));
    const second = await signIn(jsonPost({ email: "stranger@example.org" }));

    expect(first.status).toBe(202);
    expect(await first.text()).toBe(await second.text());
  });

  it("returns the unavailable response when frontend and backend disagree", async () => {
    checkAuthAgreement.mockResolvedValue({ agrees: false });
    const { signIn } = await load(true);

    const response = await signIn(jsonPost({ email: "a@example.com" }));

    expect(response.status).toBe(503);
    expect((await response.json()).detail).toBe("unavailable-marker");
    expect(requestOtp).not.toHaveBeenCalled();
  });

  it("still signs in when the backend is unreachable (agrees null)", async () => {
    checkAuthAgreement.mockResolvedValue({ agrees: null });
    const { signIn } = await load(true);

    const response = await signIn(jsonPost({ email: "a@example.com" }));

    expect(response.status).toBe(202);
    expect(requestOtp).toHaveBeenCalledTimes(1);
  });

  describe("OPENLAW_SITE_URL (magic-link redirect_to)", () => {
    it("returns 503 with helplines and never calls requestOtp when unset", async () => {
      const { signIn } = await load(true);
      vi.stubEnv("OPENLAW_SITE_URL", "");
      const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});

      const response = await signIn(jsonPost({ email: "a@example.com" }));

      expect(response.status).toBe(503);
      expectHelp(await response.json());
      expect(requestOtp).not.toHaveBeenCalled();
      expect(JSON.stringify(errorSpy.mock.calls)).toContain("OPENLAW_SITE_URL");
      errorSpy.mockRestore();
    });

    it.each([
      "javascript:alert(1)",
      "ftp://openlaw.example",
      "https://user:pw@openlaw.example",
      "https://openlaw.example/?a=b",
      "https://openlaw.example/#frag",
      "not a url",
    ])("returns 503 and never calls requestOtp for invalid value %s", async (value) => {
      const { signIn } = await load(true);
      vi.stubEnv("OPENLAW_SITE_URL", value);
      const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});

      const response = await signIn(jsonPost({ email: "a@example.com" }));

      expect(response.status).toBe(503);
      expectHelp(await response.json());
      expect(requestOtp).not.toHaveBeenCalled();
      expect(JSON.stringify(errorSpy.mock.calls)).not.toContain(value);
      errorSpy.mockRestore();
    });

    it("uses the configured URL (with path, no trailing slash) whatever the Host header", async () => {
      const { signIn } = await load(true);
      vi.stubEnv("OPENLAW_SITE_URL", "https://openlaw.example/app/");

      const response = await signIn(
        jsonPost({ email: "a@example.com" }, { ...SAME_ORIGIN, host: "evil.example" }),
      );

      expect(response.status).toBe(202);
      expect(requestOtp.mock.calls[0][1].redirectTo).toBe(
        "https://openlaw.example/app/api/auth/callback",
      );
    });
  });

  describe("request body cap", () => {
    it("returns 413 with helplines for an oversized Content-Length without reading it", async () => {
      const { signIn } = await load(true);
      const request = jsonPost({ email: "a@example.com" }, {
        ...SAME_ORIGIN,
        "content-length": "999999",
      });

      const response = await signIn(request);

      expect(response.status).toBe(413);
      expectHelp(await response.json());
      expect(requestOtp).not.toHaveBeenCalled();
    });

    it("returns 413 when the actual body exceeds the cap despite a small Content-Length", async () => {
      const { signIn } = await load(true);
      const request = new Request("http://localhost:3000/api/auth/sign-in", {
        method: "POST",
        headers: { "content-type": "application/json", ...SAME_ORIGIN },
        body: JSON.stringify({ email: "a@example.com", pad: "x".repeat(10_000) }),
      });

      const response = await signIn(request);

      expect(response.status).toBe(413);
      expect(requestOtp).not.toHaveBeenCalled();
    });

    it("runs the limiter before reading the body", async () => {
      const { signIn } = await load(true);
      for (let i = 0; i < 10; i += 1) await signIn(jsonPost({ email: "a@example.com" }));
      const request = jsonPost({ email: "a@example.com" });
      const text = vi.spyOn(request, "text");
      const json = vi.spyOn(request, "json");

      const response = await signIn(request);

      expect(response.status).toBe(429);
      expect(text).not.toHaveBeenCalled();
      expect(json).not.toHaveBeenCalled();
    });
  });

  it("counts invalid-email attempts against the limiter", async () => {
    const { signIn } = await load(true);
    for (let i = 0; i < 10; i += 1) {
      expect((await signIn(jsonPost({ email: "nope" }))).status).toBe(400);
    }

    const response = await signIn(jsonPost({ email: "valid@example.com" }));

    expect(response.status).toBe(429);
    expect(requestOtp).not.toHaveBeenCalled();
  });

  describe("per-IP limiter", () => {
    const fromIp = (ip: string | null) =>
      jsonPost(
        { email: "a@example.com" },
        ip === null ? SAME_ORIGIN : { ...SAME_ORIGIN, "x-forwarded-for": ip },
      );

    it("returns 429 with Retry-After and helplines past the per-IP ceiling", async () => {
      const { signIn } = await load(true);
      const statuses: number[] = [];
      let last: Response | null = null;
      for (let i = 0; i < 12; i += 1) {
        last = await signIn(fromIp("203.0.113.9, 10.0.0.1"));
        statuses.push(last.status);
      }

      expect(statuses.slice(0, 10).every((s) => s === 202)).toBe(true);
      expect(statuses.slice(10)).toEqual([429, 429]);
      expect(Number(last?.headers.get("retry-after"))).toBeGreaterThan(0);
      expectHelp(await last!.json());
      expect(requestOtp).toHaveBeenCalledTimes(10);
    });

    it("keys on the first X-Forwarded-For value, not the whole header", async () => {
      const { signIn } = await load(true);
      for (let i = 0; i < 10; i += 1) await signIn(fromIp("203.0.113.9"));

      const sameClient = await signIn(fromIp("203.0.113.9, 198.51.100.1"));
      const otherClient = await signIn(fromIp("203.0.113.10"));

      expect(sameClient.status).toBe(429);
      expect(otherClient.status).toBe(202);
    });

    it("shares one bucket when X-Forwarded-For is missing", async () => {
      const { signIn } = await load(true);
      for (let i = 0; i < 10; i += 1) await signIn(fromIp(null));

      const response = await signIn(fromIp(null));

      expect(response.status).toBe(429);
    });

    it("bounds the number of tracked addresses", async () => {
      const { createSignInLimiter } = await import("@/lib/auth/rateLimit");
      const limiter = createSignInLimiter({ windowSeconds: 600, maxAttempts: 1, maxKeys: 3 });

      for (let i = 0; i < 20; i += 1) limiter.check(`10.0.0.${i}`, 1000);

      expect(limiter.size()).toBeLessThanOrEqual(3);
    });
  });
});

describe("POST /api/auth/sign-in (no-JavaScript form post)", () => {
  function formPost(
    fields: Record<string, string>,
    headers: Record<string, string> = SAME_ORIGIN,
    extra: Record<string, string> = {},
  ): Request {
    return new Request("http://localhost:3000/api/auth/sign-in", {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded", ...headers, ...extra },
      body: new URLSearchParams(fields),
    });
  }

  function location(response: Response): string | null {
    return response.headers.get("location");
  }

  it("redirects 303 to /signup?sent=1 on success", async () => {
    const { signIn } = await load(true);

    const response = await signIn(formPost({ email: "form@example.com", language: "en" }));

    expect(response.status).toBe(303);
    expect(location(response)).toBe("/signup?sent=1");
    expect(response.headers.get("cache-control")).toBe("no-store");
  });

  it("carries Hindi through the redirect", async () => {
    const { signIn } = await load(true);

    const response = await signIn(formPost({ email: "form@example.com", language: "hi" }));

    expect(location(response)).toBe("/signup?sent=1&language=hi");
  });

  it("redirects identically for an existing and a brand-new address", async () => {
    const { signIn } = await load(true);
    // GoTrue answers differently per address; the redirect must not.
    requestOtp.mockImplementation(async (_config, otp) =>
      otp.identifier === "existing@example.com"
        ? { kind: "sent" as const, existingUser: true }
        : { kind: "sent" as const, createdUser: true },
    );
    const existing = await signIn(formPost({ email: "existing@example.com", language: "hi" }));
    const created = await signIn(formPost({ email: "brand-new@example.com", language: "hi" }));

    expect(requestOtp).toHaveBeenCalledTimes(2);
    expect(existing.status).toBe(created.status);
    expect(location(existing)).toBe(location(created));
    expect([...existing.headers].sort()).toEqual([...created.headers].sort());
  });

  it("answers a JSON caller identically for an existing and a brand-new address", async () => {
    const { signIn } = await load(true);
    requestOtp.mockImplementation(async (_config, otp) =>
      otp.identifier === "existing@example.com"
        ? { kind: "sent" as const, existingUser: true }
        : { kind: "sent" as const, createdUser: true },
    );
    const existing = await signIn(jsonPost({ email: "existing@example.com" }));
    const created = await signIn(jsonPost({ email: "brand-new@example.com" }));

    expect(await existing.text()).toBe(await created.text());
  });

  it("redirects an invalid email to the invalid_email code, calling nothing", async () => {
    const { signIn } = await load(true);

    const response = await signIn(formPost({ email: "not-an-email" }));

    expect(response.status).toBe(303);
    expect(location(response)).toBe("/signup?error=invalid_email");
    expect(requestOtp).not.toHaveBeenCalled();
  });

  it("redirects to error=rate_limited when GoTrue rate limits", async () => {
    requestOtp.mockResolvedValue({ kind: "rate_limited", retryAfterSeconds: 30 });
    const { signIn } = await load(true);

    const response = await signIn(formPost({ email: "a@example.com" }));

    expect(response.status).toBe(303);
    expect(location(response)).toBe("/signup?error=rate_limited");
  });

  it("redirects to error=rate_limited past the per-IP ceiling", async () => {
    const { signIn } = await load(true);
    for (let i = 0; i < 10; i += 1) await signIn(formPost({ email: "a@example.com" }));

    const response = await signIn(formPost({ email: "a@example.com" }));

    expect(response.status).toBe(303);
    expect(location(response)).toBe("/signup?error=rate_limited");
  });

  it("redirects to error=failed when GoTrue fails", async () => {
    requestOtp.mockResolvedValue({ kind: "failed", detail: "boom" });
    const { signIn } = await load(true);

    const response = await signIn(formPost({ email: "a@example.com", language: "hi" }));

    expect(location(response)).toBe("/signup?error=failed&language=hi");
  });

  it("redirects to error=failed for a cross-site POST without sending mail", async () => {
    const { signIn } = await load(true);

    const response = await signIn(
      formPost({ email: "victim@example.com" }, { "sec-fetch-site": "cross-site" }),
    );

    expect(response.status).toBe(303);
    expect(location(response)).toBe("/signup?error=failed");
    expect(requestOtp).not.toHaveBeenCalled();
  });

  it("redirects to error=too_large for an oversized body", async () => {
    const { signIn } = await load(true);

    const response = await signIn(
      formPost({ email: "a@example.com" }, SAME_ORIGIN, { "content-length": "999999" }),
    );

    expect(response.status).toBe(303);
    expect(location(response)).toBe("/signup?error=too_large");
    expect(requestOtp).not.toHaveBeenCalled();
  });

  it("redirects to error=unavailable on a mode mismatch", async () => {
    checkAuthAgreement.mockResolvedValue({ agrees: false });
    const { signIn } = await load(true);

    const response = await signIn(formPost({ email: "a@example.com" }));

    expect(response.status).toBe(303);
    expect(location(response)).toBe("/signup?error=unavailable");
  });

  it("keeps JSON callers on JSON responses", async () => {
    const { signIn } = await load(true);

    const response = await signIn(jsonPost({ email: "not-an-email" }));

    expect(response.status).toBe(400);
    expect(response.headers.get("location")).toBeNull();
  });

  it("stays a 404 in Mode A even for a form post", async () => {
    const { signIn } = await load(false);

    const response = await signIn(formPost({ email: "a@example.com" }));

    expect(response.status).toBe(404);
  });
});

describe("GET /api/auth/callback", () => {
  const expectVerifierCleared = () => {
    const writes = sets.filter((c) => c.name === "openlaw_pkce");
    expect(writes.length).toBeGreaterThan(0);
    expect(writes[writes.length - 1].options.maxAge).toBe(0);
  };
  const callbackRequest = (query = "code=abc123") =>
    new Request(`http://localhost:3000/api/auth/callback?${query}`);

  it("writes the session, clears the verifier and redirects to / on success", async () => {
    cookieHeader = "openlaw_pkce=verifier-value";
    const { callback } = await load(true);

    const response = await callback(callbackRequest());

    expect(exchangeCode.mock.calls[0][1]).toBe("abc123");
    expect(exchangeCode.mock.calls[0][2]).toBe("verifier-value");
    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe("/");
    const session = sets.find((c) => c.name === "openlaw_session");
    expect(session?.options.httpOnly).toBe(true);
    expect(JSON.parse(session!.value).email).toBe("person@example.com");
    const cleared = sets.filter((c) => c.name === "openlaw_pkce");
    expect(cleared[cleared.length - 1].options.maxAge).toBe(0);
    expect(await response.text()).not.toContain("access-token-secret");
  });

  describe("account migration", () => {
    const withCookies = "openlaw_pkce=verifier-value; openlaw_anon_id=anon-1";
    const migrationOutcomes: Array<[string, () => void]> = [
      ...[401, 409, 503].map((status): [string, () => void] => [
        `a ${status} backend response`,
        () => backendFetch.mockResolvedValue(new Response("x", { status })),
      ]),
      ["a network error", () => backendFetch.mockRejectedValue(new TypeError("fetch failed"))],
      [
        "a timeout",
        () => backendFetch.mockRejectedValue(new DOMException("timed out", "TimeoutError")),
      ],
    ];

    it("posts to /account/migrate with the in-memory bearer and only the anon cookie", async () => {
      cookieHeader = withCookies;
      const { callback } = await load(true);
      const request = new Request("http://localhost:3000/api/auth/callback?code=abc123", {
        headers: { cookie: `${withCookies}; openlaw_session=stale-session` },
      });

      const response = await callback(request);

      expect(response.headers.get("location")).toBe("/");
      expect(backendFetch).toHaveBeenCalledTimes(1);
      const [url, init] = backendFetch.mock.calls[0];
      expect(String(url)).toMatch(/\/account\/migrate$/);
      expect(init?.method).toBe("POST");
      const sent = new Headers(init?.headers);
      expect(sent.get("authorization")).toBe("Bearer access-token-secret");
      expect(sent.get("cookie")).toBe("openlaw_anon_id=anon-1");
      expect(init?.signal).toBeInstanceOf(AbortSignal);
    });

    it.each(migrationOutcomes)(
      "still signs in, logs without tokens and flags ?migration=failed on %s",
      async (_name, arrange) => {
        cookieHeader = withCookies;
        arrange();
        const errors = vi.spyOn(console, "error").mockImplementation(() => undefined);
        const { callback } = await load(true);

        const response = await callback(callbackRequest());

        expect(response.status).toBe(303);
        expect(response.headers.get("location")).toBe("/?migration=failed");
        const session = sets.find((c) => c.name === "openlaw_session");
        expect(session?.options.httpOnly).toBe(true);
        const writes = sets.filter((c) => c.name === "openlaw_pkce");
        expect(writes[writes.length - 1].options.maxAge).toBe(0);
        expect(errors).toHaveBeenCalled();
        expect(JSON.stringify(errors.mock.calls)).not.toContain("access-token-secret");
        errors.mockRestore();
      },
    );

    it.each([
      ["ok", () => new Response("{}", { status: 200 })],
      ["non-2xx", () => new Response("x", { status: 401 })],
    ])("cancels the migrate response body on a %s response", async (_n, make) => {
      cookieHeader = withCookies;
      const response = make();
      const cancel = vi.spyOn(response.body!, "cancel");
      backendFetch.mockResolvedValue(response);
      vi.spyOn(console, "error").mockImplementation(() => undefined);
      const { callback } = await load(true);

      await callback(callbackRequest());

      expect(cancel).toHaveBeenCalled();
    });

    it("still redirects to / when cancelling the body throws after a 200", async () => {
      cookieHeader = withCookies;
      const response = new Response("{}", { status: 200 });
      vi.spyOn(response.body!, "cancel").mockRejectedValue(new TypeError("cancel failed"));
      backendFetch.mockResolvedValue(response);
      const errors = vi.spyOn(console, "error").mockImplementation(() => undefined);
      const { callback } = await load(true);

      const result = await callback(callbackRequest());

      expect(result.headers.get("location")).toBe("/");
      expect(JSON.stringify(errors.mock.calls)).toContain("TypeError");
      errors.mockRestore();
    });

    it("does not call the backend when the code exchange fails", async () => {
      cookieHeader = withCookies;
      exchangeCode.mockResolvedValue({ kind: "failed", detail: "x" });
      const { callback } = await load(true);

      await callback(callbackRequest());

      expect(backendFetch).not.toHaveBeenCalled();
    });
  });

  it("redirects to /signup?error=expired for an expired code", async () => {
    cookieHeader = "openlaw_pkce=verifier-value";
    exchangeCode.mockResolvedValue({ kind: "expired" });
    const { callback } = await load(true);

    const response = await callback(callbackRequest());

    expect(response.headers.get("location")).toBe("/signup?error=expired");
    expect(sets.some((c) => c.name === "openlaw_session")).toBe(false);
  });

  it("redirects to /signup?error=failed when the exchange fails or throws", async () => {
    cookieHeader = "openlaw_pkce=verifier-value";
    const { callback } = await load(true);

    exchangeCode.mockResolvedValueOnce({ kind: "failed", detail: "x" });
    const failed = await callback(callbackRequest());
    exchangeCode.mockRejectedValueOnce(new Error("boom"));
    const thrown = await callback(callbackRequest());

    expect(failed.headers.get("location")).toBe("/signup?error=failed");
    expect(thrown.headers.get("location")).toBe("/signup?error=failed");
  });

  it("treats a missing verifier as failed without calling GoTrue", async () => {
    const { callback } = await load(true);

    const response = await callback(callbackRequest());

    expect(response.headers.get("location")).toBe("/signup?error=failed");
    expect(exchangeCode).not.toHaveBeenCalled();
    expectVerifierCleared();
  });

  it("treats a missing code as failed without calling GoTrue", async () => {
    cookieHeader = "openlaw_pkce=verifier-value";
    const { callback } = await load(true);

    const response = await callback(callbackRequest(""));

    expect(response.headers.get("location")).toBe("/signup?error=failed");
    expect(exchangeCode).not.toHaveBeenCalled();
    expectVerifierCleared();
  });

  it("answers a mode mismatch with a 303 to the sign-in page, never JSON", async () => {
    cookieHeader = "openlaw_pkce=verifier-value";
    checkAuthAgreement.mockResolvedValue({ agrees: false });
    const { callback } = await load(true);

    const response = await callback(callbackRequest());

    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe("/signup?error=unavailable");
    expect(response.headers.get("content-type")).toBeNull();
    expect(exchangeCode).not.toHaveBeenCalled();
    expectVerifierCleared();
  });

  // Final review M4: the language chosen at sign-in rides with the verifier.
  describe("the person's language", () => {
    async function signInThenCookie(language: string): Promise<string> {
      const { signIn } = await load(true);
      await signIn(jsonPost({ email: "a@b.in", language }));
      const verifier = sets.find((c) => c.name === "openlaw_pkce");
      sets.length = 0;
      return `openlaw_pkce=${encodeURIComponent(verifier!.value)}`;
    }

    it("passes only the verifier itself to GoTrue", async () => {
      cookieHeader = await signInThenCookie("hi");
      const { callback } = await load(true);

      await callback(callbackRequest());

      const sentVerifier = exchangeCode.mock.calls[0][2] as string;
      expect(sentVerifier).toMatch(/^[A-Za-z0-9_-]{43}$/);
    });

    it.each([
      ["success", () => undefined, "/?language=hi"],
      ["expired link", () => exchangeCode.mockResolvedValue({ kind: "expired" }), "/signup?error=expired&language=hi"],
      ["failed exchange", () => exchangeCode.mockResolvedValue({ kind: "failed", detail: "x" }), "/signup?error=failed&language=hi"],
      ["mode mismatch", () => checkAuthAgreement.mockResolvedValue({ agrees: false }), "/signup?error=unavailable&language=hi"],
      [
        "failed migration",
        () => backendFetch.mockImplementation(async () => new Response(null, { status: 503 })),
        "/?migration=failed&language=hi",
      ],
    ])("keeps Hindi on the %s redirect", async (_label, arrange, location) => {
      cookieHeader = await signInThenCookie("hi");
      arrange();
      const { callback } = await load(true);

      const response = await callback(callbackRequest());

      expect(response.status).toBe(303);
      expect(response.headers.get("location")).toBe(location);
    });

    it("keeps Hindi when the code is missing", async () => {
      cookieHeader = await signInThenCookie("hi");
      const { callback } = await load(true);

      const response = await callback(callbackRequest(""));

      expect(response.headers.get("location")).toBe("/signup?error=failed&language=hi");
    });

    it("stays English for an English sign-in", async () => {
      cookieHeader = await signInThenCookie("en");
      const { callback } = await load(true);

      expect((await callback(callbackRequest())).headers.get("location")).toBe("/");
    });
  });

  it("still exchanges when the backend is unreachable (agrees null)", async () => {
    cookieHeader = "openlaw_pkce=verifier-value";
    checkAuthAgreement.mockResolvedValue({ agrees: null });
    const { callback } = await load(true);

    const response = await callback(callbackRequest());

    expect(response.headers.get("location")).toBe("/");
  });
});

describe("POST /api/auth/sign-out", () => {
  const signedIn = `openlaw_session=${encodeURIComponent(
    JSON.stringify({
      accessToken: "access-token-secret",
      refreshToken: "r",
      expiresAt: NOW_SECONDS + 3600,
      email: "person@example.com",
    }),
  )}`;

  it("revokes best-effort, clears the cookie and redirects to /", async () => {
    cookieHeader = signedIn;
    const { signOut } = await load(true);

    const response = await signOut(jsonPost({}));

    expect(revokeSession).toHaveBeenCalledTimes(1);
    expect(revokeSession.mock.calls[0][1]).toBe("access-token-secret");
    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe("/");
    expect(sets.find((c) => c.name === "openlaw_session")?.options.maxAge).toBe(0);
  });

  it("clears the cookie even when revokeSession throws", async () => {
    cookieHeader = signedIn;
    revokeSession.mockRejectedValue(new Error("gotrue down"));
    const { signOut } = await load(true);

    const response = await signOut(jsonPost({}));

    expect(response.status).toBe(303);
    expect(sets.find((c) => c.name === "openlaw_session")?.options.maxAge).toBe(0);
  });

  it("clears the cookie without calling GoTrue when there is no session", async () => {
    const { signOut } = await load(true);

    const response = await signOut(jsonPost({}));

    expect(revokeSession).not.toHaveBeenCalled();
    expect(response.status).toBe(303);
    expect(sets.find((c) => c.name === "openlaw_session")?.options.maxAge).toBe(0);
  });

  // Final review M3: the next person on a shared device must not inherit
  // this browser's anonymous identity (and so its anonymous uploads).
  it.each([
    ["signed in", () => signedIn],
    ["with no session", () => ""],
  ])("expires openlaw_anon_id with the backend's attributes (%s)", async (_label, cookie) => {
    cookieHeader = `openlaw_anon_id=anon-1; ${cookie()}`;
    const { signOut } = await load(true);

    await signOut(jsonPost({}));

    const anon = sets.find((c) => c.name === "openlaw_anon_id");
    expect(anon?.value).toBe("");
    expect(anon?.options).toMatchObject({ maxAge: 0, httpOnly: true, sameSite: "lax", path: "/", secure: true });
  });

  it("expires openlaw_anon_id even when revokeSession throws", async () => {
    cookieHeader = signedIn;
    revokeSession.mockRejectedValue(new Error("gotrue down"));
    const { signOut } = await load(true);

    await signOut(jsonPost({}));

    expect(sets.find((c) => c.name === "openlaw_anon_id")?.options.maxAge).toBe(0);
  });

  it("drops Secure from the expiry only when OPENLAW_COOKIE_SECURE=false, as the backend does", async () => {
    vi.stubEnv("OPENLAW_COOKIE_SECURE", "false");
    const { signOut } = await load(true);

    await signOut(jsonPost({}));

    expect(sets.find((c) => c.name === "openlaw_anon_id")?.options.secure).toBe(false);
  });

  it("returns 403 for a cross-site POST and does not sign anyone out", async () => {
    cookieHeader = signedIn;
    const { signOut } = await load(true);

    const response = await signOut(jsonPost({}, { "sec-fetch-site": "cross-site" }));

    expect(response.status).toBe(403);
    expect(revokeSession).not.toHaveBeenCalled();
    expect(sets).toHaveLength(0);
  });
});

describe("GET /api/auth/session", () => {
  const sessionRequest = () => new Request("http://localhost:3000/api/auth/session");

  it("returns exactly signedIn and email, and no token", async () => {
    cookieHeader = `openlaw_session=${encodeURIComponent(
      JSON.stringify({
        accessToken: "access-token-secret",
        refreshToken: "refresh-token-secret",
        expiresAt: NOW_SECONDS + 3600,
        email: "person@example.com",
      }),
    )}`;
    const { session } = await load(true);

    const response = await session(sessionRequest());
    const text = await response.text();

    expect(Object.keys(JSON.parse(text)).sort()).toEqual(["email", "signedIn"]);
    expect(JSON.parse(text)).toEqual({ signedIn: true, email: "person@example.com" });
    expect(text).not.toContain("secret");
    expect(response.headers.get("cache-control")).toBe("no-store");
  });

  it("returns signedIn false and a null email without a session", async () => {
    const { session } = await load(true);

    const response = await session(sessionRequest());

    expect(await response.json()).toEqual({ signedIn: false, email: null });
  });
});
