// @vitest-environment node
import { readdirSync, statSync } from "node:fs";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

const METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"];

type Handler = (req: Request, ctx: { params: Promise<Record<string, string>> }) => Promise<Response>;

const ROUTES: {
  dir: string;
  mod: string;
  methods: string[];
  params: Record<string, string>;
  backend: string;
}[] = [
  { dir: "ask", mod: "../openlaw/ask/route", methods: ["POST"], params: {}, backend: "/ask" },
  { dir: "documents", mod: "../openlaw/documents/route", methods: ["POST"], params: {}, backend: "/documents" },
  {
    dir: "documents/[documentId]",
    mod: "../openlaw/documents/[documentId]/route",
    methods: ["DELETE"],
    params: { documentId: "d1" },
    backend: "/documents/d1",
  },
  {
    dir: "conversations/[conversationId]",
    mod: "../openlaw/conversations/[conversationId]/route",
    methods: ["GET", "DELETE"],
    params: { conversationId: "c1" },
    backend: "/conversations/c1",
  },
];

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("enumerated proxy routes", () => {
  for (const route of ROUTES) {
    describe(`/api/openlaw/${route.dir}`, () => {
      it("exports exactly the methods the backend route needs, force-dynamic", async () => {
        const mod = (await import(/* @vite-ignore */ route.mod)) as Record<string, unknown>;
        const exported = METHODS.filter((m) => typeof mod[m] === "function").sort();
        expect(exported).toEqual([...route.methods].sort());
        expect(mod.dynamic).toBe("force-dynamic");
      });

      for (const method of route.methods) {
        it(`${method} forwards to backend ${route.backend}`, async () => {
          vi.stubEnv("OPENLAW_API_URL", "http://backend.test");
          const fetchMock = vi.fn(async (..._args: unknown[]) => new Response(null, { status: 204 }));
          vi.stubGlobal("fetch", fetchMock);
          const mod = (await import(/* @vite-ignore */ route.mod)) as Record<string, Handler>;
          const init: RequestInit = { method };
          if (method === "POST") init.body = new FormData();
          const response = await mod[method](
            new Request("http://web/api/openlaw/x", init),
            { params: Promise.resolve(route.params) },
          );
          expect(response.status).toBe(204);
          expect(fetchMock.mock.calls[0][0]).toBe(`http://backend.test${route.backend}`);
          expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe(method);
        });
      }
    });
  }

  it("encodes a dynamic segment so it cannot escape its backend path", async () => {
    vi.stubEnv("OPENLAW_API_URL", "http://backend.test");
    const fetchMock = vi.fn(async (..._args: unknown[]) => new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    const mod = await import("../openlaw/conversations/[conversationId]/route");
    await mod.GET(new Request("http://web/x"), {
      params: Promise.resolve({ conversationId: "../readyz" }),
    });
    expect(fetchMock.mock.calls[0][0]).toBe("http://backend.test/conversations/..%2Freadyz");
  });
});

describe("proxy surface is enumerated", () => {
  const APP_DIR = path.resolve(__dirname, "../..");

  function walk(dir: string, prefix = ""): string[] {
    return readdirSync(dir).flatMap((name) => {
      const full = path.join(dir, name);
      const rel = prefix ? `${prefix}/${name}` : name;
      return statSync(full).isDirectory() ? walk(full, rel) : [rel];
    });
  }

  // The four proxy routes, Task 3's own (non-proxy) auth-status handler, and
  // Task 8's four auth routes (each 404s in Mode A, none proxies to the backend).
  const ALLOWED_ROUTE_FILES = [
    "api/auth/callback/route.ts",
    "api/auth/session/route.ts",
    "api/auth/sign-in/route.ts",
    "api/auth/sign-out/route.ts",
    "api/openlaw/ask/route.ts",
    "api/openlaw/auth-status/route.ts",
    "api/openlaw/conversations/[conversationId]/route.ts",
    "api/openlaw/documents/[documentId]/route.ts",
    "api/openlaw/documents/route.ts",
  ];

  it("has no catch-all segment anywhere under app/ (would expose /readyz)", () => {
    const catchAll = walk(APP_DIR).filter((f) => /\[\[?\.\.\./.test(f));
    expect(catchAll, "catch-all would re-expose /readyz (M7)").toEqual([]);
  });

  it("has exactly the allowed route.ts files (never proxy /readyz)", () => {
    const routes = walk(APP_DIR)
      .filter((f) => /(^|\/)route\.(ts|tsx|js)$/.test(f))
      .sort();
    expect(routes, "unexpected route file; /readyz must stay unreachable").toEqual(ALLOWED_ROUTE_FILES);
  });
});
