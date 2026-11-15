import { readFileSync } from "node:fs";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  exchangeCode,
  requestOtp,
  revokeSession,
  type GotrueConfig,
  type OtpRequest,
} from "@/lib/auth/gotrue";

// Obviously fake values -- no real credential appears in this file.
const API_KEY = "fake-api-key-for-tests-0123456789";
const VERIFIER = "fake-verifier-for-tests-abcdefghijklmnopqrstuvwxyz";
const CODE = "fake-magic-link-code-1234";
const CONFIG: GotrueConfig = { supabaseUrl: "https://proj.supabase.co", apiKey: API_KEY };
const OTP: OtpRequest = {
  method: "email",
  identifier: "asha@example.com",
  codeChallenge: "challenge-value",
  redirectTo: "https://openlaw.in/api/auth/callback",
};

function b64url(value: object): string {
  return btoa(JSON.stringify(value)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
function makeJwt(claims: object): string {
  return `${b64url({ alg: "HS256" })}.${b64url(claims)}.c2ln`;
}
const GOOD_CLAIMS = { sub: "user-1", aud: "authenticated", exp: 1_900_000_000, role: "authenticated" };
const ACCESS = makeJwt(GOOD_CLAIMS);
const REFRESH = "fake-refresh-token-xyz";
const goodBody = {
  access_token: ACCESS,
  refresh_token: REFRESH,
  expires_at: 1_900_000_000,
  user: { email: "asha@example.com" },
};

function reply(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  const text = typeof body === "string" ? body : JSON.stringify(body);
  const isEmpty = status === 204;
  return new Response(isEmpty ? null : text, { status, headers });
}

const fetchMock = vi.fn<typeof fetch>();
const consoleSpies = ["log", "error", "warn", "info", "debug"].map((m) =>
  vi.spyOn(console, m as "log"),
);

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.unstubAllGlobals();
  consoleSpies.forEach((s) => expect(s).not.toHaveBeenCalled());
});

function lastCall(): { url: URL; init: RequestInit } {
  const [url, init] = fetchMock.mock.calls[0];
  return { url: new URL(String(url)), init: init as RequestInit };
}

describe("requestOtp", () => {
  it("returns sent and posts PKCE S256 to /auth/v1/otp", async () => {
    fetchMock.mockResolvedValue(reply(200, {}));
    expect(await requestOtp(CONFIG, OTP)).toEqual({ kind: "sent" });
    const { url, init } = lastCall();
    expect(url.pathname).toBe("/auth/v1/otp");
    expect(url.searchParams.get("redirect_to")).toBe(OTP.redirectTo);
    expect(JSON.parse(String(init.body))).toMatchObject({
      email: OTP.identifier,
      code_challenge: "challenge-value",
      code_challenge_method: "s256",
    });
    expect(init.redirect).toBe("error");
    expect(init.signal).toBeInstanceOf(AbortSignal);
  });

  it("parses Retry-After on 429", async () => {
    fetchMock.mockResolvedValue(reply(429, {}, { "Retry-After": "42" }));
    expect(await requestOtp(CONFIG, OTP)).toEqual({ kind: "rate_limited", retryAfterSeconds: 42 });
  });

  it.each([[null], ["soon"], ["-5"], ["99999999"]])(
    "gives null for unusable Retry-After %s",
    async (header) => {
      fetchMock.mockResolvedValue(
        reply(429, {}, header === null ? {} : { "Retry-After": header }),
      );
      expect(await requestOtp(CONFIG, OTP)).toEqual({ kind: "rate_limited", retryAfterSeconds: null });
    },
  );

  it("fails on 4xx/5xx with a status and safe code only", async () => {
    fetchMock.mockResolvedValue(
      reply(422, { error_code: "validation_failed", msg: `echo ${API_KEY} ${CODE}` }),
    );
    const outcome = await requestOtp(CONFIG, OTP);
    expect(outcome).toEqual({ kind: "failed", detail: "gotrue_http_422:validation_failed" });
    fetchMock.mockResolvedValue(reply(500, "<html>boom</html>"));
    expect(await requestOtp(CONFIG, OTP)).toEqual({ kind: "failed", detail: "gotrue_http_500" });
  });

  it("ignores a hostile error_code", async () => {
    fetchMock.mockResolvedValue(reply(400, { error_code: `Bad ${API_KEY}` }));
    expect(await requestOtp(CONFIG, OTP)).toEqual({ kind: "failed", detail: "gotrue_http_400" });
  });

  it("fails cleanly when the network throws, without echoing the error", async () => {
    fetchMock.mockRejectedValue(new TypeError(`connect failed ${API_KEY}`));
    const outcome = await requestOtp(CONFIG, OTP);
    expect(outcome).toEqual({ kind: "failed", detail: "gotrue_unreachable" });
  });

  it("reports a timeout distinctly", async () => {
    fetchMock.mockRejectedValue(new DOMException("slow", "TimeoutError"));
    expect(await requestOtp(CONFIG, OTP)).toEqual({ kind: "failed", detail: "gotrue_timeout" });
  });

  it.each(["http://proj.supabase.co", "not a url", ""])(
    "refuses an unsafe base URL %j without any fetch",
    async (supabaseUrl) => {
      const outcome = await requestOtp({ ...CONFIG, supabaseUrl }, OTP);
      expect(outcome).toEqual({ kind: "failed", detail: "gotrue_url_invalid" });
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it("allows plain http for localhost", async () => {
    fetchMock.mockResolvedValue(reply(200, {}));
    await requestOtp({ ...CONFIG, supabaseUrl: "http://localhost:54321" }, OTP);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("refuses an unsupported method", async () => {
    const outcome = await requestOtp(CONFIG, { ...OTP, method: "phone" as "email" });
    expect(outcome).toEqual({ kind: "failed", detail: "unsupported_method" });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("exchangeCode", () => {
  // Final review L1: the refresh token is neither required nor kept.
  it("ignores the refresh token entirely", async () => {
    fetchMock.mockResolvedValue(reply(200, { ...goodBody, refresh_token: undefined }));
    const outcome = await exchangeCode(CONFIG, CODE, VERIFIER);
    expect(outcome.kind).toBe("session");
    expect(JSON.stringify(outcome)).not.toContain(REFRESH);
  });

  it("returns a full session and posts to the pkce grant", async () => {
    fetchMock.mockResolvedValue(reply(200, goodBody));
    expect(await exchangeCode(CONFIG, CODE, VERIFIER)).toEqual({
      kind: "session",
      accessToken: ACCESS,
      expiresAt: 1_900_000_000,
      email: "asha@example.com",
    });
    const { url, init } = lastCall();
    expect(url.pathname).toBe("/auth/v1/token");
    expect(url.searchParams.get("grant_type")).toBe("pkce");
    expect(JSON.parse(String(init.body))).toEqual({ auth_code: CODE, code_verifier: VERIFIER });
  });

  it("falls back to the token's exp when expires_at is absent", async () => {
    fetchMock.mockResolvedValue(reply(200, { ...goodBody, expires_at: undefined }));
    const outcome = await exchangeCode(CONFIG, CODE, VERIFIER);
    expect(outcome).toMatchObject({ kind: "session", expiresAt: 1_900_000_000 });
  });

  it.each(["flow_state_not_found", "flow_state_expired", "otp_expired"])(
    "maps %s to expired",
    async (error_code) => {
      fetchMock.mockResolvedValue(reply(400, { error_code }));
      expect(await exchangeCode(CONFIG, CODE, VERIFIER)).toEqual({ kind: "expired" });
    },
  );

  it("treats other errors as failed", async () => {
    fetchMock.mockResolvedValue(reply(400, { error_code: "bad_code_verifier" }));
    expect(await exchangeCode(CONFIG, CODE, VERIFIER)).toEqual({
      kind: "failed",
      detail: "gotrue_http_400:bad_code_verifier",
    });
    fetchMock.mockResolvedValue(reply(502, "gateway"));
    expect(await exchangeCode(CONFIG, CODE, VERIFIER)).toEqual({
      kind: "failed",
      detail: "gotrue_http_502",
    });
  });

  it("fails on network error", async () => {
    fetchMock.mockRejectedValue(new Error("down"));
    expect(await exchangeCode(CONFIG, CODE, VERIFIER)).toEqual({
      kind: "failed",
      detail: "gotrue_unreachable",
    });
  });

  const invalidBodies: Array<[string, unknown]> = [
    ["a non-JSON body", "<html>"],
    ["an array", []],
    ["a missing access token", { ...goodBody, access_token: undefined }],
    ["a non-JWT access token", { ...goodBody, access_token: "opaque" }],
    ["an undecodable JWT payload", { ...goodBody, access_token: "a.!!!.c" }],
    ["a JWT whose payload is not an object", { ...goodBody, access_token: `a.${b64url([1])}.c` }],
    ["no subject", { ...goodBody, access_token: makeJwt({ ...GOOD_CLAIMS, sub: undefined }) }],
    ["the wrong audience", { ...goodBody, access_token: makeJwt({ ...GOOD_CLAIMS, aud: "anon" }) }],
    ["no expiry anywhere", { ...goodBody, expires_at: undefined, access_token: makeJwt({ ...GOOD_CLAIMS, exp: undefined }) }],
    ["a string expiry", { ...goodBody, expires_at: "soon", access_token: makeJwt({ ...GOOD_CLAIMS, exp: "x" }) }],
    ["no user", { ...goodBody, user: undefined }],
    ["a blank email", { ...goodBody, user: { email: "  " } }],
  ];
  it.each(invalidBodies)("fails closed on %s without leaking tokens", async (_name, body) => {
    fetchMock.mockResolvedValue(reply(200, body));
    const outcome = await exchangeCode(CONFIG, CODE, VERIFIER);
    expect(outcome).toEqual({ kind: "failed", detail: "gotrue_response_invalid" });
    const text = JSON.stringify(outcome);
    for (const secret of [ACCESS, REFRESH, API_KEY, CODE, VERIFIER]) {
      expect(text).not.toContain(secret);
    }
  });

  it("fails closed on an oversized body", async () => {
    fetchMock.mockResolvedValue(reply(200, " ".repeat(70 * 1024) + JSON.stringify(goodBody)));
    expect(await exchangeCode(CONFIG, CODE, VERIFIER)).toEqual({
      kind: "failed",
      detail: "gotrue_response_invalid",
    });
  });

  it("never leaks secrets in any failure outcome", async () => {
    const leaky = { error_code: "x", msg: `${ACCESS} ${REFRESH} ${API_KEY} ${CODE} ${VERIFIER}` };
    fetchMock.mockResolvedValue(reply(400, leaky));
    const a = JSON.stringify(await exchangeCode(CONFIG, CODE, VERIFIER));
    fetchMock.mockRejectedValue(new Error(`${ACCESS} ${REFRESH} ${API_KEY}`));
    const b = JSON.stringify(await exchangeCode(CONFIG, CODE, VERIFIER));
    for (const text of [a, b]) {
      for (const secret of [ACCESS, REFRESH, API_KEY, CODE, VERIFIER]) {
        expect(text).not.toContain(secret);
      }
    }
  });
});

describe("revokeSession", () => {
  it("posts a local-scope logout with the bearer token", async () => {
    fetchMock.mockResolvedValue(reply(204, ""));
    await revokeSession(CONFIG, ACCESS);
    const { url, init } = lastCall();
    expect(url.pathname).toBe("/auth/v1/logout");
    expect(url.searchParams.get("scope")).toBe("local");
    expect((init.headers as Record<string, string>).Authorization).toBe(`Bearer ${ACCESS}`);
  });

  it("never throws, even when GoTrue is down", async () => {
    fetchMock.mockRejectedValue(new Error("down"));
    await expect(revokeSession(CONFIG, ACCESS)).resolves.toBeUndefined();
  });
});

describe("module hygiene", () => {
  const source = readFileSync(
    path.resolve(import.meta.dirname, "../auth/gotrue.ts"),
    "utf8",
  );

  it("is guarded by server-only as its first import", () => {
    expect(source.match(/^import .*$/m)?.[0]).toBe('import "server-only";');
  });

  it("only targets the three GoTrue endpoints and never logs", () => {
    const targets = [...source.matchAll(/["`](\/auth\/v1\/[a-z]+)/g)].map((m) => m[1]);
    expect([...new Set(targets)].sort()).toEqual(["/auth/v1/logout", "/auth/v1/otp", "/auth/v1/token"]);
    expect(source).not.toMatch(/console\./);
    expect(source).not.toMatch(/use client/);
  });
});
