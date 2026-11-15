import { describe, expect, it } from "vitest";

import { authRefusal, signInSentResponse } from "@/lib/auth/authRefusal";
import { SIGN_IN_WINDOW_SECONDS } from "@/lib/auth/rateLimit";
import { getStrings } from "@/lib/strings";

describe("sign-in refusal copy", () => {
  it.each(["en", "hi"] as const)("reads its sentences from strings.ts in %s", async (language) => {
    const body = await authRefusal("failed", language, 503).json();

    expect(body.detail).toBe(getStrings(language).authFailed);
    expect(body.escalation_footer).toBeTruthy();
    expect(body.helplines.length).toBeGreaterThan(0);
  });

  it("states a rate-limit wait derived from the limiter's window constant", () => {
    const minutes = SIGN_IN_WINDOW_SECONDS / 60;

    expect(Number.isInteger(minutes)).toBe(true);
    expect(getStrings("en").authRateLimited).toContain(`in ${minutes} minutes`);
    expect(getStrings("hi").authRateLimited).toContain(`${minutes} मिनट`);
  });

  it("returns the same sentence for the 202 as the strings dictionary", async () => {
    const body = await signInSentResponse("hi").json();

    expect(body.detail).toBe(getStrings("hi").authLinkSent);
  });

  it("never reuses the English sentence for Hindi on any new key", () => {
    const en = getStrings("en");
    const hi = getStrings("hi");
    for (const key of [
      "authLinkSent", "authLinkExpired", "authFailed", "authRateLimited", "authUnavailable",
      "authMigrationFailed", "accountDeleted", "accountDeleteFailed",
      "signInRequired", "accountDeleteSignInRequired", "accountEmailConflict",
    ] as const) {
      expect(hi[key], key).not.toBe(en[key]);
      expect(hi[key], key).toMatch(/[ऀ-ॿ]/);
    }
  });
});
