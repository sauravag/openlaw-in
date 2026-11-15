import { describe, expect, it } from "vitest";
import nextConfig, { buildEnv } from "../next.config";

describe("next.config env", () => {
  it("defines the auth flag as the literal false when unset", () => {
    expect(buildEnv({})).toEqual({ NEXT_PUBLIC_OPENLAW_AUTH_ENABLED: "false" });
  });

  it("passes the flag through unchanged when set", () => {
    expect(buildEnv({ NEXT_PUBLIC_OPENLAW_AUTH_ENABLED: "true" })).toEqual({
      NEXT_PUBLIC_OPENLAW_AUTH_ENABLED: "true",
    });
  });

  it("registers the env on the exported config", () => {
    expect(typeof nextConfig.env?.NEXT_PUBLIC_OPENLAW_AUTH_ENABLED).toBe("string");
  });
});
