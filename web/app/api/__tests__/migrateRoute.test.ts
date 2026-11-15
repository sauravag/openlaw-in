// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getStrings } from "@/lib/strings";

// Final review H2: a failed migration can be retried from the page.
let cookieHeader = "";
vi.mock("next/headers", () => ({
  cookies: async () => ({ set: () => undefined }),
  headers: async () => new Headers(cookieHeader ? { cookie: cookieHeader } : {}),
}));

const backendFetch = vi.fn<typeof fetch>();
const NOW_SECONDS = Math.floor(Date.now() / 1000);
const SESSION_COOKIE = `openlaw_session=${encodeURIComponent(
  JSON.stringify({ accessToken: "access-token-secret", refreshToken: "r", expiresAt: NOW_SECONDS + 3600, email: "a@b.in" }),
)}`;
const SAME_ORIGIN = { "sec-fetch-site": "same-origin" };

async function load(authEnabled: boolean) {
  vi.resetModules();
  vi.stubEnv("NEXT_PUBLIC_OPENLAW_AUTH_ENABLED", authEnabled ? "true" : "false");
  vi.stubEnv("OPENLAW_API_URL", "http://backend.test");
  return (await import("../auth/migrate/route")).POST;
}

function post(body: unknown, headers: Record<string, string> = SAME_ORIGIN): Request {
  return new Request("http://localhost:3000/api/auth/migrate", {
    method: "POST",
    headers: { "content-type": "application/json", cookie: cookieHeader, ...headers },
    body: JSON.stringify(body),
  });
}

async function expectRefusal(response: Response, status: number, detail: string) {
  expect(response.status).toBe(status);
  const body = await response.json();
  expect(body.detail).toBe(detail);
  expect(body.escalation_footer.length).toBeGreaterThan(0);
  expect(body.helplines.length).toBeGreaterThan(0);
}

beforeEach(() => {
  cookieHeader = `openlaw_anon_id=anon-1; ${SESSION_COOKIE}`;
  backendFetch
    .mockReset()
    .mockImplementation(async () => Response.json({ conversations_moved: 2, documents_moved: 1 }));
  vi.stubGlobal("fetch", backendFetch);
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("POST /api/auth/migrate", () => {
  it("is a bare 404 in Mode A and calls nothing", async () => {
    const POST = await load(false);

    const response = await POST(post({}));

    expect(response.status).toBe(404);
    expect(backendFetch).not.toHaveBeenCalled();
  });

  it("refuses a cross-site POST before reading the session", async () => {
    const POST = await load(true);

    const response = await POST(post({}, { "sec-fetch-site": "cross-site", origin: "https://evil.example" }));

    expect(response.status).toBe(403);
    expect((await response.json()).helplines.length).toBeGreaterThan(0);
    expect(backendFetch).not.toHaveBeenCalled();
  });

  it("refuses with the sign-in copy and help when there is no session", async () => {
    cookieHeader = "openlaw_anon_id=anon-1";
    const POST = await load(true);

    const response = await POST(post({ language: "hi" }));

    await expectRefusal(response, 401, getStrings("hi").signInRequired);
    expect(response.headers.get("www-authenticate")).toBe("Bearer");
    expect(backendFetch).not.toHaveBeenCalled();
  });

  it("calls /account/migrate with the session's token and only the anon cookie", async () => {
    const POST = await load(true);

    const response = await POST(post({ language: "en" }));

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ status: "migrated", conversations_moved: 2, documents_moved: 1 });
    expect(response.headers.get("cache-control")).toBe("no-store");
    const [url, init] = backendFetch.mock.calls[0];
    expect(url).toBe("http://backend.test/account/migrate");
    const headers = new Headers((init as RequestInit).headers);
    expect((init as RequestInit).method).toBe("POST");
    expect(headers.get("authorization")).toBe("Bearer access-token-secret");
    expect(headers.get("cookie")).toBe("openlaw_anon_id=anon-1");
  });

  it.each([
    [401, 401, "signInRequired"],
    [409, 409, "accountEmailConflict"],
    [503, 502, "authMigrationFailed"],
  ] as const)("maps a backend %s to a %s refusal with help", async (upstream, status, key) => {
    backendFetch.mockResolvedValue(Response.json({ detail: "x" }, { status: upstream }));
    const POST = await load(true);

    await expectRefusal(await POST(post({ language: "hi" })), status, getStrings("hi")[key]);
  });

  it("reports a backend that cannot be reached as a failure with help", async () => {
    backendFetch.mockRejectedValue(new TypeError("connect ECONNREFUSED"));
    const POST = await load(true);

    await expectRefusal(await POST(post({})), 502, getStrings("en").authMigrationFailed);
  });

  it("is force-dynamic and exports POST only", async () => {
    vi.resetModules();
    const mod = (await import("../auth/migrate/route")) as Record<string, unknown>;
    expect(mod.dynamic).toBe("force-dynamic");
    expect(["GET", "PUT", "DELETE", "PATCH"].filter((m) => typeof mod[m] === "function")).toEqual([]);
  });
});
