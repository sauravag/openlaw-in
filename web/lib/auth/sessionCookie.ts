import "server-only";

/**
 * Pure (no `next/headers`) half of the session cookie: its name, its stored
 * shape, and the one parser every reader uses. Split out so `backend.ts` (the
 * proxy) and `session.ts` (the cookie jar) share a single definition and a
 * single notion of "a valid session" without the proxy pulling in request
 * context. `session.ts` re-exports everything public from here.
 */

/** Cookie holding the Supabase session. HttpOnly, SameSite=Lax, Path=/,
 * Secure unless OPENLAW_COOKIE_SECURE=false (plain-HTTP local development --
 * the same reason `Settings.cookie_secure` cannot be unconditional). */
export const SESSION_COOKIE_NAME = "openlaw_session";

/** A session this close to `expiresAt` counts as expired, so the proxy never
 * races the backend's own `exp` check. */
export const SESSION_EXPIRY_SKEW_SECONDS = 60;

export interface StoredSession {
  readonly accessToken: string;
  readonly refreshToken: string;
  /** Unix seconds. */
  readonly expiresAt: number;
  readonly email: string;
}

/**
 * Decoded value of cookie `name` in a raw `Cookie` header, or null when it is
 * absent, empty, undecodable, or present MORE THAN ONCE. Duplicates mean
 * ambiguous identity: a sibling subdomain can "toss" its own copy of a cookie
 * and browsers order the copies unpredictably, so no copy is trusted.
 */
export function readCookieValue(cookieHeader: string | null, name: string): string | null {
  if (!cookieHeader) return null;
  const values: string[] = [];
  for (const part of cookieHeader.split(";")) {
    const index = part.indexOf("=");
    if (index === -1 || part.slice(0, index).trim() !== name) continue;
    values.push(part.slice(index + 1).trim());
  }
  if (values.length !== 1 || values[0] === "") return null;
  try {
    return decodeURIComponent(values[0]);
  } catch {
    return null;
  }
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value !== "";
}

/**
 * Parses a decoded cookie value into a session, or null when it is missing,
 * malformed JSON, has a wrong-typed field, or is expired (including skew).
 */
export function parseSession(
  raw: string | undefined | null,
  nowSeconds: number = Math.floor(Date.now() / 1000),
): StoredSession | null {
  if (!raw) return null;
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof data !== "object" || data === null) return null;
  const { accessToken, refreshToken, expiresAt, email } = data as Record<string, unknown>;
  if (
    !isNonEmptyString(accessToken) ||
    !isNonEmptyString(refreshToken) ||
    !isNonEmptyString(email) ||
    typeof expiresAt !== "number" ||
    !Number.isFinite(expiresAt)
  ) {
    return null;
  }
  if (expiresAt - SESSION_EXPIRY_SKEW_SECONDS <= nowSeconds) return null;
  return { accessToken, refreshToken, expiresAt, email };
}

/** Session carried by a raw `Cookie` header, or null. */
export function sessionFromCookieHeader(cookieHeader: string | null): StoredSession | null {
  return parseSession(readCookieValue(cookieHeader, SESSION_COOKIE_NAME) ?? undefined);
}
