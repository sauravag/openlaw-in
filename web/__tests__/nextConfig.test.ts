import { describe, expect, it } from "vitest";
import nextConfig, { buildEnv } from "../next.config";
import { TRUTHY_AUTH_FLAG_VALUES } from "../lib/authFlag";

const TRUTHY = TRUTHY_AUTH_FLAG_VALUES.flatMap((value) => [value, value.toUpperCase(), value[0].toUpperCase() + value.slice(1)]);

describe("next.config env", () => {
  it("defines the auth flag as the literal false when unset", () => {
    expect(buildEnv({})).toEqual({ NEXT_PUBLIC_OPENLAW_AUTH_ENABLED: "false" });
  });

  // Final review H3a: the backend's pydantic bool reads all of these as true;
  // a frontend that read only "true" built Mode A beside a Mode B backend.
  it.each(TRUTHY)("normalises %j to the literal true", (value) => {
    expect(buildEnv({ NEXT_PUBLIC_OPENLAW_AUTH_ENABLED: value })).toEqual({
      NEXT_PUBLIC_OPENLAW_AUTH_ENABLED: "true",
    });
  });

  it.each(["false", "0", "no", "off", "", " true", "2", "enabled"])(
    "normalises %j to the literal false",
    (value) => {
      expect(buildEnv({ NEXT_PUBLIC_OPENLAW_AUTH_ENABLED: value })).toEqual({
        NEXT_PUBLIC_OPENLAW_AUTH_ENABLED: "false",
      });
    },
  );

  it("registers the env on the exported config", () => {
    expect(["true", "false"]).toContain(nextConfig.env?.NEXT_PUBLIC_OPENLAW_AUTH_ENABLED);
  });
});
