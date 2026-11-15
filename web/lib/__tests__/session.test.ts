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

const NOW = 1_800_000_000;
const SESSION = {
  accessToken: "fake-access-token",
  refreshToken: "fake-refresh-token",
  expiresAt: NOW + 3600,
  email: "person@example.com",
};

async function load() {
  vi.resetModules();
  return import("@/lib/auth/session");
}

/** The Cookie header a browser would send back for the last cookie written. */
function echoLast(): string {
  const last = sets[sets.length - 1];
  return `${last.name}=${last.value}`;
}

beforeEach(() => {
  sets.length = 0;
  cookieHeader = "";
  vi.useFakeTimers();
  vi.setSystemTime(NOW * 1000);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllEnvs();
});

describe("cookie flags", () => {
  it("sets the session cookie httpOnly, lax, path=/, secure by default", async () => {
    const { writeSession, SESSION_COOKIE_NAME } = await load();
    await writeSession(SESSION);
    expect(sets[0].name).toBe(SESSION_COOKIE_NAME);
    expect(sets[0].options.httpOnly).toBe(true);
    expect(sets[0].options.sameSite).toBe("lax");
    expect(sets[0].options.path).toBe("/");
    expect(sets[0].options.secure).toBe(true);
  });

  it("drops secure only when OPENLAW_COOKIE_SECURE is exactly 'false'", async () => {
    vi.stubEnv("OPENLAW_COOKIE_SECURE", "false");
    const { writeSession } = await load();
    await writeSession(SESSION);
    expect(sets[0].options.secure).toBe(false);
    vi.stubEnv("OPENLAW_COOKIE_SECURE", "0");
    const again = await load();
    await again.writeSession(SESSION);
    expect(sets[1].options.secure).toBe(true);
  });

  it("sets the verifier cookie httpOnly, lax, path=/, 600s, secure", async () => {
    const { writeVerifier, PKCE_COOKIE_NAME } = await load();
    await writeVerifier("fake-verifier");
    expect(sets[0].name).toBe(PKCE_COOKIE_NAME);
    expect(sets[0].options.httpOnly).toBe(true);
    expect(sets[0].options.sameSite).toBe("lax");
    expect(sets[0].options.path).toBe("/");
    expect(sets[0].options.secure).toBe(true);
    expect(sets[0].options.maxAge).toBe(600);
  });

  it("clearSession writes an expired, empty cookie with the same flags", async () => {
    const { clearSession } = await load();
    await clearSession();
    expect(sets[0].value).toBe("");
    expect(sets[0].options.maxAge).toBe(0);
    expect(sets[0].options.expires).toEqual(new Date(0));
    expect(sets[0].options.httpOnly).toBe(true);
    expect(sets[0].options.sameSite).toBe("lax");
    expect(sets[0].options.path).toBe("/");
    expect(sets[0].options.secure).toBe(true);
  });

  it("clearVerifier writes an expired, empty cookie", async () => {
    const { clearVerifier } = await load();
    await clearVerifier();
    expect(sets[0].value).toBe("");
    expect(sets[0].options.maxAge).toBe(0);
    expect(sets[0].options.httpOnly).toBe(true);
  });
});

describe("readSession / writeSession", () => {
  it("round-trips all four fields", async () => {
    const { writeSession, readSession } = await load();
    await writeSession(SESSION);
    cookieHeader = echoLast();
    expect(await readSession()).toEqual(SESSION);
  });

  it("returns null when the cookie is missing", async () => {
    const { readSession } = await load();
    expect(await readSession()).toBeNull();
  });

  it("returns null for malformed JSON", async () => {
    const { readSession, SESSION_COOKIE_NAME } = await load();
    cookieHeader = `${SESSION_COOKIE_NAME}=${encodeURIComponent("{not json")}`;
    expect(await readSession()).toBeNull();
  });

  it("returns null for an expired session", async () => {
    const { writeSession, readSession } = await load();
    await writeSession({ ...SESSION, expiresAt: NOW - 1 });
    cookieHeader = echoLast();
    expect(await readSession()).toBeNull();
  });

  it("returns null within 60 seconds of expiry and a session just beyond", async () => {
    const { writeSession, readSession } = await load();
    await writeSession({ ...SESSION, expiresAt: NOW + 60 });
    cookieHeader = echoLast();
    expect(await readSession()).toBeNull();
    await writeSession({ ...SESSION, expiresAt: NOW + 61 });
    cookieHeader = echoLast();
    expect(await readSession()).not.toBeNull();
  });

  it("treats duplicate session cookies as no session", async () => {
    const { writeSession, readSession } = await load();
    await writeSession(SESSION);
    cookieHeader = `${echoLast()}; ${echoLast()}`;
    expect(await readSession()).toBeNull();
  });

  it("throws a clear error instead of writing an oversized cookie", async () => {
    const { writeSession } = await load();
    await expect(
      writeSession({ ...SESSION, accessToken: "a".repeat(4000) }),
    ).rejects.toThrow(/too large/i);
    expect(sets).toHaveLength(0);
  });
});

describe("verifier", () => {
  it("round-trips the verifier and returns null when absent", async () => {
    const { writeVerifier, readVerifier } = await load();
    expect(await readVerifier()).toBeNull();
    await writeVerifier("fake-verifier");
    cookieHeader = echoLast();
    expect(await readVerifier()).toBe("fake-verifier");
  });
});

describe("parseSession", () => {
  it("rejects undefined, garbage, and wrong field types", async () => {
    const { parseSession } = await load();
    expect(parseSession(undefined)).toBeNull();
    expect(parseSession("garbage")).toBeNull();
    expect(parseSession("null")).toBeNull();
    expect(parseSession(JSON.stringify({ ...SESSION, expiresAt: "9999999999" }))).toBeNull();
    expect(parseSession(JSON.stringify({ ...SESSION, accessToken: 1 }))).toBeNull();
    expect(parseSession(JSON.stringify({ ...SESSION, refreshToken: null }))).toBeNull();
    expect(parseSession(JSON.stringify({ ...SESSION, email: undefined }))).toBeNull();
    expect(parseSession(JSON.stringify({ ...SESSION, accessToken: "" }))).toBeNull();
  });

  it("accepts a valid session and honours an explicit clock", async () => {
    const { parseSession } = await load();
    const raw = JSON.stringify(SESSION);
    expect(parseSession(raw, NOW)).toEqual(SESSION);
    expect(parseSession(raw, SESSION.expiresAt - 60)).toBeNull();
  });
});
