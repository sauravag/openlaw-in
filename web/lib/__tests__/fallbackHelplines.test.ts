import { describe, expect, it } from "vitest";
import { getFallbackHelplineSet } from "../fallbackHelplines";

describe("getFallbackHelplineSet", () => {
  it("returns the English set by default", () => {
    const set = getFallbackHelplineSet("en");
    expect(set.crisisResponse).toMatch(/could not reach the server/i);
    expect(set.helplines).toContainEqual({ number: "112", label: "Emergency" });
  });

  it("returns the Hindi set for language 'hi'", () => {
    const set = getFallbackHelplineSet("hi");
    expect(set.crisisResponse).toMatch(/सर्वर से जुड़ नहीं सके/);
    expect(set.helplines).toContainEqual({ number: "112", label: "आपातकालीन सेवा" });
  });

  it("never claims to have answered the question", () => {
    for (const language of ["en", "hi"]) {
      const set = getFallbackHelplineSet(language);
      expect(set.crisisResponse.toLowerCase()).not.toMatch(/notice must|deposit must/);
    }
  });

  it("gives every helpline number an ASCII tel:-compatible value", () => {
    for (const language of ["en", "hi"]) {
      for (const helpline of getFallbackHelplineSet(language).helplines) {
        expect(helpline.number).toMatch(/^\d+$/);
      }
    }
  });
});
