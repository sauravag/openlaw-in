import { afterEach, describe, expect, it, vi } from "vitest";

async function loadFlag(value: string | undefined): Promise<boolean> {
  vi.resetModules();
  if (value === undefined) {
    vi.stubEnv("NEXT_PUBLIC_OPENLAW_AUTH_ENABLED", "");
    delete process.env.NEXT_PUBLIC_OPENLAW_AUTH_ENABLED;
  } else {
    vi.stubEnv("NEXT_PUBLIC_OPENLAW_AUTH_ENABLED", value);
  }
  const mod = await import("../authMode");
  return mod.AUTH_ENABLED;
}

describe("AUTH_ENABLED", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("is false when unset", async () => {
    expect(await loadFlag(undefined)).toBe(false);
  });

  it.each(["false", "1", "TRUE", "yes", ""])("is false for %j", async (v) => {
    expect(await loadFlag(v)).toBe(false);
  });

  it("is true only for the exact string true", async () => {
    expect(await loadFlag("true")).toBe(true);
  });
});
