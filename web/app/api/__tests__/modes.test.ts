// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import { importInMode, runMode, setAuthMode } from "@/test-utils/authMode";

// Never reached over a network: every outbound call lands on this mock.
const backendFetch = vi.fn<typeof fetch>();

vi.mock("next/headers", () => ({
  cookies: async () => ({ set: () => undefined }),
  headers: async () => new Headers(),
}));

const SAME_ORIGIN = { "sec-fetch-site": "same-origin" };
const NOW_SECONDS = Math.floor(Date.now() / 1000);
const STALE_SESSION_COOKIE = `openlaw_session=${encodeURIComponent(
  JSON.stringify({
    accessToken: "stale-access-token",
    refreshToken: "stale-refresh-token",
    expiresAt: NOW_SECONDS + 3600,
    email: "person@example.com",
  }),
)}`;

/** Every `/api/auth/*` route handler, as `[path, loader, method]`. */
const AUTH_ROUTES: ReadonlyArray<readonly [string, () => Promise<object>, string]> = [
  ["/api/auth/sign-in", () => import("../auth/sign-in/route"), "POST"],
  ["/api/auth/callback", () => import("../auth/callback/route"), "GET"],
  ["/api/auth/sign-out", () => import("../auth/sign-out/route"), "POST"],
  ["/api/auth/session", () => import("../auth/session/route"), "GET"],
  ["/api/auth/delete-account", () => import("../auth/delete-account/route"), "POST"],
];

type Handler = (request: Request) => Promise<Response>;

function requestFor(path: string, method: string): Request {
  return new Request(`http://localhost:3000${path}`, {
    method,
    headers: { "content-type": "application/json", ...SAME_ORIGIN },
    body: method === "POST" ? "{}" : undefined,
  });
}

function stubEnvironment(): void {
  vi.stubEnv("OPENLAW_API_URL", "http://backend.test");
  vi.stubEnv("OPENLAW_SUPABASE_URL", "https://project.supabase.test");
  vi.stubEnv("OPENLAW_SUPABASE_ANON_KEY", "dummy-anon-key");
  vi.stubEnv("OPENLAW_SITE_URL", "https://openlaw.example");
  backendFetch.mockReset().mockImplementation(async () => Response.json({}));
  vi.stubGlobal("fetch", backendFetch);
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("this run's mode", () => {
  it("is the mode the build-time flag says, as the app reads it", async () => {
    // Re-reads the flag from the environment the run was started with.
    vi.resetModules();
    const { AUTH_ENABLED } = await import("@/lib/authMode");

    expect(AUTH_ENABLED).toBe(runMode() === "b");
  });
});

describe.each(AUTH_ROUTES)("%s", (path, load, method) => {
  it("is a bare 404 in Mode A and calls nothing", async () => {
    stubEnvironment();
    const mod = (await importInMode(false, load)) as Record<string, Handler>;

    const response = await mod[method](requestFor(path, method));

    expect(response.status).toBe(404);
    expect(await response.text()).toBe("");
    expect(backendFetch).not.toHaveBeenCalled();
  });

  it("is a real route in Mode B (anything but 404)", async () => {
    stubEnvironment();
    const mod = (await importInMode(true, load)) as Record<string, Handler>;

    const response = await mod[method](requestFor(path, method));

    expect(response.status).not.toBe(404);
  });
});

describe("proxyToBackend and a session cookie", () => {
  async function proxiedHeaders(authEnabled: boolean): Promise<Headers> {
    stubEnvironment();
    setAuthMode(authEnabled);
    const { proxyToBackend } = await import("@/lib/backend");
    const request = new Request("http://localhost:3000/api/openlaw/ask", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        cookie: `openlaw_anon_id=anon-1; ${STALE_SESSION_COOKIE}`,
        authorization: "Bearer browser-supplied-token",
        ...SAME_ORIGIN,
      },
      body: "{}",
    });

    await proxyToBackend(request, "/ask");

    expect(backendFetch).toHaveBeenCalledTimes(1);
    return new Headers((backendFetch.mock.calls[0][1] as RequestInit).headers);
  }

  it("attaches no Authorization in Mode A, even with a stale Mode B cookie", async () => {
    const headers = await proxiedHeaders(false);

    expect(headers.get("authorization")).toBeNull();
    // The anonymous identity still rides along; the session never does.
    expect(headers.get("cookie")).toBe("openlaw_anon_id=anon-1");
  });

  it("attaches the session's bearer in Mode B and drops the browser's own", async () => {
    const headers = await proxiedHeaders(true);

    expect(headers.get("authorization")).toBe("Bearer stale-access-token");
    expect(headers.get("cookie")).toBe("openlaw_anon_id=anon-1");
  });
});
