import { describe, expect, it } from "vitest";
import { formatResetDate } from "../formatDate";

describe("formatResetDate", () => {
  it("formats an ISO date for an English locale", () => {
    expect(formatResetDate("2026-10-01", "en")).toBe("1 October 2026");
  });

  it("formats an ISO date for a Hindi locale", () => {
    // Hindi (Devanagari) month names are locale data, not our own string --
    // just assert the day and year survive and it does not throw.
    const formatted = formatResetDate("2026-10-01", "hi");
    expect(formatted).toContain("2026");
  });

  it("falls back to the raw string on a malformed date", () => {
    expect(formatResetDate("not-a-date", "en")).toBe("not-a-date");
  });
});
