import "server-only";

import { cookies, headers } from "next/headers";

import { ANON_COOKIE_NAME } from "@/lib/anonCookie";
import { normalizeLanguage, type AuthLanguage } from "@/lib/auth/authRefusal";

import {
  readCookieValue,
  PKCE_COOKIE_NAME,
  SESSION_COOKIE_NAME,
  sessionFromCookieHeader,
  type StoredSession,
} from "@/lib/auth/sessionCookie";

export { parseSession, SESSION_COOKIE_NAME } from "@/lib/auth/sessionCookie";
export type { StoredSession } from "@/lib/auth/sessionCookie";

export { PKCE_COOKIE_NAME } from "@/lib/auth/sessionCookie";
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

// NOTE: `headers()` reflects the inbound request only; it does not see a
// cookie set earlier in the same request (see
// next/dist/server/async-storage/request-store.js). A read must never follow a
// write within one request.
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

/**
 * Expires the backend's anonymous-identity cookie (final review M3), with the
 * attributes `openlaw.api.app` sets it with: HttpOnly, SameSite=Lax, Path=/,
 * Secure unless `OPENLAW_COOKIE_SECURE=false`. On sign-out this hands the next
 * person on a shared device a fresh anonymous identity instead of the last
 * one's, so material uploaded while a session had lapsed cannot follow them.
 */
export async function clearAnonymousCookie(): Promise<void> {
  const jar = await cookies();
  jar.set(ANON_COOKIE_NAME, "", { ...baseOptions(), maxAge: 0, expires: new Date(0) });
}

// NOTE: `headers()` reflects the inbound request only; it does not see a
// cookie set earlier in the same request (see
// next/dist/server/async-storage/request-store.js). A read must never follow a
// write within one request.
/** What the sign-in request left for the callback: the PKCE verifier, and
 * the language the person was reading in (final review M4), so every
 * callback redirect and refusal can stay in that language. */
export interface PendingSignIn {
  readonly verifier: string;
  readonly language: AuthLanguage;
}

/** Separates verifier and language in the cookie value. Never part of a
 * base64url verifier, so the split is unambiguous. */
const LANGUAGE_SEPARATOR = ".";

export async function readVerifier(): Promise<PendingSignIn | null> {
  const raw = await readRawCookie(PKCE_COOKIE_NAME);
  if (!raw) return null;
  const index = raw.lastIndexOf(LANGUAGE_SEPARATOR);
  // A value without a language (set before this change) is English.
  if (index === -1) return { verifier: raw, language: "en" };
  const verifier = raw.slice(0, index);
  return verifier ? { verifier, language: normalizeLanguage(raw.slice(index + 1)) } : null;
}

export async function writeVerifier(verifier: string, language: AuthLanguage): Promise<void> {
  const jar = await cookies();
  jar.set(PKCE_COOKIE_NAME, `${verifier}${LANGUAGE_SEPARATOR}${language}`, {
    ...baseOptions(),
    maxAge: PKCE_COOKIE_MAX_AGE_SECONDS,
  });
}

export async function clearVerifier(): Promise<void> {
  const jar = await cookies();
  jar.set(PKCE_COOKIE_NAME, "", { ...baseOptions(), maxAge: 0, expires: new Date(0) });
}
