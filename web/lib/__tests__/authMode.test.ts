import { afterEach, describe, expect, it, vi } from "vitest";
import { isAuthFlagOn, TRUTHY_AUTH_FLAG_VALUES } from "../authMode";

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

describe("AUTH_ENABLED (reads the build-normalised literal)", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("is false when unset", async () => {
    expect(await loadFlag(undefined)).toBe(false);
  });

  it("is false for the normalised false", async () => {
    expect(await loadFlag("false")).toBe(false);
  });

  it("is true for the normalised true", async () => {
    expect(await loadFlag("true")).toBe(true);
  });
});

// Final review H3a: exactly the spellings the backend's pydantic bool reads as
// true, case-insensitive (pinned by tests/test_auth_flag_parity.py).
describe("isAuthFlagOn", () => {
  it.each(TRUTHY_AUTH_FLAG_VALUES.flatMap((v) => [v, v.toUpperCase(), v[0].toUpperCase() + v.slice(1)]))(
    "is true for %j",
    (value) => {
      expect(isAuthFlagOn(value)).toBe(true);
    },
  );

  it.each([undefined, "false", "0", "no", "off", "f", "n", "", " true", "true ", "2", "enabled"])(
    "is false for %j",
    (value) => {
      expect(isAuthFlagOn(value)).toBe(false);
    },
  );
});
