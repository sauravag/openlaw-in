import { describe, expect, it } from "vitest";
import { splitOnHelplineNumbers } from "../helplineNumbers";

describe("splitOnHelplineNumbers", () => {
  it("flags a known helpline number embedded in a sentence", () => {
    const segments = splitOnHelplineNumbers("Call 112 now.");
    expect(segments).toEqual([
      { text: "Call ", isPhoneNumber: false },
      { text: "112", isPhoneNumber: true },
      { text: " now.", isPhoneNumber: false },
    ]);
  });

  it("flags every helpline number when several appear in one string", () => {
    const segments = splitOnHelplineNumbers(
      "call 112. The women's helpline is 181, and the police women's helpline is 1091.",
    );
    const flagged = segments.filter((segment) => segment.isPhoneNumber).map((s) => s.text);
    expect(flagged).toEqual(["112", "181", "1091"]);
  });

  it("does not flag a year or other non-helpline number", () => {
    const segments = splitOnHelplineNumbers(
      "the Legal Services Authorities Act, 1987 covers section 12.",
    );
    expect(segments.some((segment) => segment.isPhoneNumber)).toBe(false);
  });

  it("returns the whole string unflagged when no helpline number is present", () => {
    expect(splitOnHelplineNumbers("no numbers here")).toEqual([
      { text: "no numbers here", isPhoneNumber: false },
    ]);
  });
});
