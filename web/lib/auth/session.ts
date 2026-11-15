import "server-only";

import { cookies, headers } from "next/headers";

import {
  readCookieValue,
  SESSION_COOKIE_NAME,
  sessionFromCookieHeader,
  type StoredSession,
} from "@/lib/auth/sessionCookie";

export { parseSession, SESSION_COOKIE_NAME } from "@/lib/auth/sessionCookie";
export type { StoredSession } from "@/lib/auth/sessionCookie";

/** Short-lived cookie holding the PKCE verifier between the sign-in request
 * and the callback. HttpOnly, SameSite=Lax, 10 minutes, deleted on exchange. */
export const PKCE_COOKIE_NAME = "openlaw_pkce";
const PKCE_COOKIE_MAX_AGE_SECONDS = 600;

/** The session cookie is JSON holding BOTH the access and refresh tokens, and
 * browsers silently drop a cookie over roughly 4 KB. Supabase JWTs are about
 * 1 KB each, so a normal session is ~2.5 KB; refuse loudly past this ceiling
 * (below 4096, leaving room for the name and attributes) rather than let the
 * browser discard the cookie and leave the person mysteriously signed out. */
const MAX_COOKIE_VALUE_BYTES = 3800;

// Not encrypted, deliberately. The cookie is HttpOnly and only travels
// server<->browser; the access token inside is already a signed JWT the
// backend verifies independently. An app-level encryption key would add a
// secret to manage without closing any path open to an attacker who can
// already read this cookie.

/** `OPENLAW_COOKIE_SECURE=false` is the only way to drop `Secure` (plain-HTTP
 * local dev). Server-side env only: never a `NEXT_PUBLIC_` variant. */
function isSecure(): boolean {
  return process.env.OPENLAW_COOKIE_SECURE !== "false";
}

function baseOptions() {
  return {
    httpOnly: true,
    sameSite: "lax" as const,
    path: "/",
    secure: isSecure(),
  };
}

async function readRawCookie(name: string): Promise<string | null> {
  return readCookieValue((await headers()).get("cookie"), name);
}

/** The signed-in session, or null (absent, malformed, duplicated or expired). */
export async function readSession(): Promise<StoredSession | null> {
  return sessionFromCookieHeader((await headers()).get("cookie"));
}

export async function writeSession(session: StoredSession): Promise<void> {
  const value = JSON.stringify({
    accessToken: session.accessToken,
    refreshToken: session.refreshToken,
    expiresAt: session.expiresAt,
    email: session.email,
  });
  if (encodeURIComponent(value).length > MAX_COOKIE_VALUE_BYTES) {
    throw new Error(
      `Session cookie too large (over ${MAX_COOKIE_VALUE_BYTES} bytes); browsers would drop it.`,
    );
  }
  const jar = await cookies();
  const maxAge = Math.max(0, session.expiresAt - Math.floor(Date.now() / 1000));
  jar.set(SESSION_COOKIE_NAME, value, { ...baseOptions(), maxAge });
}

/** Overwrites with an already-expired cookie so no stale copy survives. */
export async function clearSession(): Promise<void> {
  const jar = await cookies();
  jar.set(SESSION_COOKIE_NAME, "", { ...baseOptions(), maxAge: 0, expires: new Date(0) });
}

export async function readVerifier(): Promise<string | null> {
  return readRawCookie(PKCE_COOKIE_NAME);
}

export async function writeVerifier(verifier: string): Promise<void> {
  const jar = await cookies();
  jar.set(PKCE_COOKIE_NAME, verifier, {
    ...baseOptions(),
    maxAge: PKCE_COOKIE_MAX_AGE_SECONDS,
  });
}

export async function clearVerifier(): Promise<void> {
  const jar = await cookies();
  jar.set(PKCE_COOKIE_NAME, "", { ...baseOptions(), maxAge: 0, expires: new Date(0) });
}
