import { describe, expect, it } from "vitest";
import { stripAuthCookies } from "@/lib/auth/sessionCookie";

describe("stripAuthCookies", () => {
  it("returns null for a missing or empty header", () => {
    expect(stripAuthCookies(null)).toBeNull();
    expect(stripAuthCookies("")).toBeNull();
  });

  it("removes the session and PKCE cookies and keeps the rest", () => {
    expect(stripAuthCookies("a=1; openlaw_session=s; openlaw_pkce=p; b=2")).toBe("a=1; b=2");
  });

  it("returns null when nothing else remains", () => {
    expect(stripAuthCookies("openlaw_session=s; openlaw_pkce=p")).toBeNull();
  });

  it("keeps cookies whose names merely START with or CONTAIN an auth name", () => {
    expect(stripAuthCookies("xopenlaw_session=1; openlaw_session2=2; openlaw_pkce_extra=3")).toBe(
      "xopenlaw_session=1; openlaw_session2=2; openlaw_pkce_extra=3",
    );
  });

  it("strips every copy of a duplicated auth cookie", () => {
    expect(
      stripAuthCookies("openlaw_session=one; keep=1; openlaw_session=two; openlaw_pkce=p; openlaw_pkce=q"),
    ).toBe("keep=1");
  });

  it("keeps a value containing '=' intact, byte for byte", () => {
    expect(stripAuthCookies("token=abc==; openlaw_session=s; q=a=b=c")).toBe("token=abc==; q=a=b=c");
  });

  it("does not treat an auth name appearing inside another cookie's value as that cookie", () => {
    expect(stripAuthCookies("note=openlaw_session=1")).toBe("note=openlaw_session=1");
  });

  it("matches names exactly, tolerating whitespace around the pair", () => {
    expect(stripAuthCookies("  openlaw_session =s;a=1")).toBe("a=1");
  });
});
