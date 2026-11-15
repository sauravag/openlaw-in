// @vitest-environment node
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { describe, expect, it } from "vitest";
import { FORBIDDEN, scan } from "../scripts/check-mode-a-bundle.mjs";

function fixture(files: Record<string, string>): string {
  const dir = mkdtempSync(join(tmpdir(), "mode-a-"));
  mkdirSync(join(dir, "static"), { recursive: true });
  mkdirSync(join(dir, "server", "app"), { recursive: true });
  for (const [path, text] of Object.entries(files)) {
    mkdirSync(dirname(join(dir, path)), { recursive: true });
    writeFileSync(join(dir, path), text);
  }
  return dir;
}

describe("check-mode-a-bundle scan", () => {
  it("passes a clean build", () => {
    const dir = fixture({ "static/chunks/a.js": "ok", "server/app/index.html": "<p>hi</p>" });

    expect(scan(dir)).toEqual({ scanned: 2, hits: [] });
  });

  it.each(FORBIDDEN as string[])("catches %s in a client chunk", (needle) => {
    const dir = fixture({ "static/chunks/a.js": `x="${needle}"` });

    expect(scan(dir).hits).toHaveLength(1);
  });

  it.each(["index.html", "index.rsc", "page.body"])("catches sign-in markup in prerendered %s", (name) => {
    const dir = fixture({ [`server/app/${name}`]: '<form action="/api/auth/sign-out">' });

    expect(scan(dir).hits).toEqual([`server/app/${name}: /api/auth/sign-out`]);
  });

  it("ignores route-handler output under server/app/api", () => {
    const dir = fixture({
      "static/chunks/a.js": "ok",
      "server/app/api/auth/sign-in/route.js": 'cookie("openlaw_pkce")',
      "server/app/api/auth/sign-in/route.body": "/api/auth/sign-in",
    });

    expect(scan(dir).hits).toEqual([]);
  });
});
