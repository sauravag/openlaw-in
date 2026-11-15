// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import { importInMode } from "@/test-utils/authMode";

const METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"];
const load = () => import("../openlaw/conversations/route");

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

function stubBackend(response: Response) {
  vi.stubEnv("OPENLAW_API_URL", "http://backend.test");
  const fetchMock = vi.fn(async (..._args: unknown[]) => response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("/api/openlaw/conversations (final review H1b)", () => {
  it("exports GET only, force-dynamic", async () => {
    const mod = (await load()) as Record<string, unknown>;
    expect(METHODS.filter((m) => typeof mod[m] === "function")).toEqual(["GET"]);
    expect(mod.dynamic).toBe("force-dynamic");
  });

  it("is a bare 404 in Mode A and never calls the backend", async () => {
    const fetchMock = stubBackend(Response.json({}));
    const mod = await importInMode(false, load);

    const response = await mod.GET(new Request("http://web/api/openlaw/conversations"));

    expect(response.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards to the backend list, query string intact, in Mode B", async () => {
    const fetchMock = stubBackend(Response.json({ conversations: [], next_offset: null }));
    const mod = await importInMode(true, load);

    const response = await mod.GET(
      new Request("http://web/api/openlaw/conversations?limit=5&offset=10&language=hi"),
    );

    expect(response.status).toBe(200);
    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://backend.test/conversations?limit=5&offset=10&language=hi",
    );
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("GET");
  });

  it("passes the backend's help-carrying 401 through for an anonymous caller", async () => {
    const refusal = {
      detail: "Sign in to see your conversations.",
      escalation_footer: "NALSA 15100",
      crisis_response: null,
      helplines: [{ number: "15100", label: "NALSA" }],
    };
    stubBackend(Response.json(refusal, { status: 401 }));
    const mod = await importInMode(true, load);

    const response = await mod.GET(new Request("http://web/api/openlaw/conversations"));

    expect(response.status).toBe(401);
    expect((await response.json()).helplines).toEqual(refusal.helplines);
  });
});
