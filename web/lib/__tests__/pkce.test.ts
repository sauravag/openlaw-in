import { describe, expect, it } from "vitest";

import { challengeFor, createVerifier } from "@/lib/auth/pkce";

// RFC 7636 section 4.1: unreserved characters, 43..128 long.
const UNRESERVED = /^[A-Za-z0-9\-._~]{43,128}$/;

describe("createVerifier", () => {
  it("produces an RFC 7636 compliant verifier", () => {
    expect(createVerifier()).toMatch(UNRESERVED);
  });

  it("uses base64url with no padding", () => {
    expect(createVerifier()).not.toMatch(/[+/=]/);
  });

  it("never repeats", () => {
    const seen = new Set(Array.from({ length: 200 }, () => createVerifier()));
    expect(seen.size).toBe(200);
  });
});

describe("challengeFor", () => {
  it("matches the RFC 7636 appendix B known answer (S256)", async () => {
    const verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk";
    expect(await challengeFor(verifier)).toBe(
      "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
    );
  });

  it("is deterministic and unpadded", async () => {
    const verifier = createVerifier();
    const first = await challengeFor(verifier);
    expect(await challengeFor(verifier)).toBe(first);
    expect(first).toMatch(/^[A-Za-z0-9_-]{43}$/);
  });
});
