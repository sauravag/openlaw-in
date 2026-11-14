import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type Mode = boolean;

function backendReports(authEnabled: boolean): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({ auth_enabled: authEnabled, supabase_url: null }),
    ),
  );
}

async function callRoute(frontendBuiltWithAuth: Mode, times = 1) {
  vi.resetModules();
  vi.stubEnv(
    "NEXT_PUBLIC_OPENLAW_AUTH_ENABLED",
    frontendBuiltWithAuth ? "true" : "false",
  );
  const route = await import("../openlaw/auth-status/route");
  // One module instance for every call: the log-once latch lives in it.
  let response = await route.GET();
  for (let i = 1; i < times; i += 1) response = await route.GET();
  return response.json();
}

describe("GET /api/openlaw/auth-status", () => {
  let errorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  // backend x frontend; the diagonal agrees.
  it.each([
    { backend: false, frontend: false, agrees: true },
    { backend: true, frontend: true, agrees: true },
    { backend: false, frontend: true, agrees: false },
    { backend: true, frontend: false, agrees: false },
  ])(
    "backend=$backend frontend=$frontend -> agrees=$agrees",
    async ({ backend, frontend, agrees }) => {
      backendReports(backend);

      const body = await callRoute(frontend);

      expect(body).toEqual({
        backendAuthEnabled: backend,
        frontendAuthEnabled: frontend,
        agrees,
      });
      expect(errorSpy).toHaveBeenCalledTimes(agrees ? 0 : 1);
    },
  );

  it("logs one explicit line naming both variables, once across many requests", async () => {
    backendReports(false);

    await callRoute(true, 5);

    expect(errorSpy).toHaveBeenCalledTimes(1);
    const line = String(errorSpy.mock.calls[0][0]);
    expect(line).toContain("OPENLAW_AUTH_ENABLED (backend)");
    expect(line).toContain("NEXT_PUBLIC_OPENLAW_AUTH_ENABLED (frontend build)");
    expect(line).toContain("rebuild the frontend or change the backend setting");
  });

  it("reports an unreachable backend as unknown, not as a mismatch", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("ECONNREFUSED");
      }),
    );

    const body = await callRoute(true);

    expect(body.agrees).toBeNull();
    expect(body.backendAuthEnabled).toBeNull();
    expect(errorSpy).not.toHaveBeenCalled();
  });
});
