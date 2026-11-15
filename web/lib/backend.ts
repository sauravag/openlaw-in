import "server-only";

import { sessionFromCookieHeader, stripAuthCookies } from "@/lib/auth/sessionCookie";
import { AUTH_ENABLED } from "@/lib/authMode";
import { isCrossSiteWrite } from "@/lib/csrf";

const DEFAULT_API_URL = "http://127.0.0.1:8000";
const BAD_GATEWAY = 502;
const BAD_REQUEST = 400;
const FORBIDDEN = 403;
const MAX_DECODE_ROUNDS = 3;
/** Bound on a server-initiated backend call, so sign-in never hangs on it. */
export const BACKEND_POST_TIMEOUT_MS = 10_000;

// RFC 9110 section 7.6.1 hop-by-hop headers (+ `host`, which fetch recomputes).
// undici rejects several of these outright, which would turn a legitimate
// chunked or keep-alive request into a 502.
const HOP_BY_HOP_HEADERS = [
  "connection", "keep-alive", "proxy-connection", "transfer-encoding", "te",
  "trailer", "upgrade", "expect", "host",
];

/**
 * Inbound headers made safe to forward: any client Authorization out, hop-by-hop
 * headers out, and the session/PKCE cookies stripped from `Cookie`. Carries NO
 * bearer; callers attach one from a session they have verified themselves.
 */
function forwardableHeaders(source: Headers): Headers {
  const headers = new Headers(source);
  // A browser must not hand the backend a token of its choosing through here.
  headers.delete("authorization");
  // Names listed in the inbound `Connection` value are hop-by-hop too.
  const named = (source.get("connection") ?? "")
    .split(",")
    .map((token) => token.trim().toLowerCase())
    .filter(Boolean);
  for (const name of [...HOP_BY_HOP_HEADERS, ...named]) headers.delete(name);
  // The backend never needs the session cookie (it holds the refresh token)
  // or the PKCE verifier; only a Bearer access token, and only in Mode B.
  // Everything else (e.g. `openlaw_anon_id`) passes through intact.
  const forwardedCookies = stripAuthCookies(source.get("cookie"));
  if (forwardedCookies === null) headers.delete("cookie");
  else headers.set("cookie", forwardedCookies);
  return headers;
}

/** Request headers for the backend: cookie in, any client Authorization out. */
function buildBackendHeaders(request: Request): Headers {
  const headers = forwardableHeaders(request.headers);
  if (AUTH_ENABLED) {
    // The cookie holds a JSON session; only the access token goes upstream,
    // and only when the session is valid, unexpired and unambiguous.
    const session = sessionFromCookieHeader(request.headers.get("cookie"));
    if (session) headers.set("authorization", `Bearer ${session.accessToken}`);
  }
  return headers;
}

/** Response headers for the browser: all Set-Cookie values kept separate. */
function buildResponseHeaders(upstream: Response, backendOrigin: string): Headers {
  const headers = new Headers();
  upstream.headers.forEach((value, key) => {
    if (key === "location" && pointsAtOrigin(value, backendOrigin)) return;
    // `forEach` flattens Set-Cookie; re-added individually below. fetch has
    // already decoded the body, so its length/encoding no longer apply.
    if (["set-cookie", "content-encoding", "content-length", "transfer-encoding"].includes(key)) return;
    headers.set(key, value);
  });
  for (const cookie of upstream.headers.getSetCookie()) {
    headers.append("set-cookie", cookie);
  }
  return headers;
}

/** True when `location` is an absolute URL on the backend's own origin. */
function pointsAtOrigin(location: string, origin: string): boolean {
  try {
    return new URL(location).origin === origin;
  } catch {
    return false;
  }
}

/** True when any path segment is `.` or `..` after (repeated) percent-decoding. */
function hasDotSegment(backendPath: string): boolean {
  return backendPath.split("/").some((raw) => {
    let segment = raw;
    for (let round = 0; round <= MAX_DECODE_ROUNDS; round += 1) {
      if (segment === "." || segment === "..") return true;
      try {
        const decoded = decodeURIComponent(segment);
        if (decoded === segment) return false;
        segment = decoded;
      } catch {
        return true; // malformed escape: refuse rather than guess
      }
    }
    return true; // still changing after several rounds: refuse
  });
}

/**
 * Proxies one request to the FastAPI backend, server-side.
 *
 * Replaces what `next.config.ts`'s `rewrites()` did, because a rewrite cannot
 * add a request header and `Authorization: Bearer` must be attached here --
 * never in the browser, where the token would have to be JavaScript-readable.
 * Proxying (rather than calling the API cross-origin) is also what lets the
 * anonymous-quota cookie (`openlaw_anon_id`, HttpOnly) survive with zero CORS
 * configuration: the browser only ever talks to its own origin.
 *
 * Three things a rewrite did for free and this MUST do explicitly:
 *   1. Forward the inbound `Cookie` header, so `openlaw_anon_id` reaches
 *      `openlaw.api.app._ensure_anonymous_token`.
 *   2. Forward every `Set-Cookie` from the backend response, so a first-time
 *      caller's freshly minted anon cookie actually lands in the browser --
 *      including on a refusal, which is precisely the case that middleware
 *      exists to cover.
 *   3. Preserve the status code and `Retry-After`, so a 429's structured
 *      refusal body and retry hint survive the hop.
 * Dropping (1) or (2) makes every request look like a first visit: anonymous
 * ownership and quota silently reset per request. That is wrong, not
 * imprecise -- see `app.py`'s docstring on the same boundary.
 */
export async function proxyToBackend(
  request: Request,
  backendPath: string,
): Promise<Response> {
  if (isCrossSiteWrite(request)) {
    return Response.json({ detail: "Forbidden." }, { status: FORBIDDEN });
  }
  if (hasDotSegment(backendPath)) {
    return Response.json({ detail: "Bad request." }, { status: BAD_REQUEST });
  }
  const base = (process.env.OPENLAW_API_URL ?? DEFAULT_API_URL).replace(/\/+$/, "");
  const search = new URL(request.url).search;
  const hasBody = request.method !== "GET" && request.method !== "HEAD";

  const init: RequestInit & { duplex?: "half" } = {
    method: request.method,
    headers: buildBackendHeaders(request),
    redirect: "manual",
    cache: "no-store",
    signal: request.signal,
  };
  if (hasBody && request.body) {
    // Streamed, not buffered: a 10 MiB upload must not become a string.
    init.body = request.body;
    init.duplex = "half";
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${base}${backendPath}${search}`, init);
  } catch (error) {
    console.error("[backend proxy] upstream request failed", backendPath, error);
    return Response.json({ detail: "The service is unavailable." }, { status: BAD_GATEWAY });
  }

  const nullBody = upstream.status === 204 || upstream.status === 304;
  return new Response(nullBody ? null : upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: buildResponseHeaders(upstream, new URL(base).origin),
  });
}

export interface PostToBackendOptions {
  /** Access token held in memory by the caller (not read from any cookie). */
  bearer: string;
  /** The inbound `Cookie` header; only non-auth cookies are forwarded. */
  inboundCookieHeader: string | null;
  timeoutMs?: number;
}

/**
 * Server-initiated, body-less POST to the backend with an explicit bearer.
 *
 * For the magic-link callback, which has only just minted the session in THIS
 * request: `proxyToBackend` reads the bearer from the inbound request's
 * cookie, which cannot see a cookie written earlier in the same request, and
 * the inbound request is a GET from an email link. Shares header building
 * with `proxyToBackend`, so `openlaw_session` and `openlaw_pkce` are never
 * forwarded, whereas `openlaw_anon_id` is. Rejects on a network error or
 * timeout; the CALLER decides what a failure means.
 */
export async function postToBackend(
  backendPath: string,
  { bearer, inboundCookieHeader, timeoutMs = BACKEND_POST_TIMEOUT_MS }: PostToBackendOptions,
): Promise<Response> {
  if (hasDotSegment(backendPath)) throw new Error("invalid backend path");
  const base = (process.env.OPENLAW_API_URL ?? DEFAULT_API_URL).replace(/\/+$/, "");
  const source = new Headers();
  if (inboundCookieHeader) source.set("cookie", inboundCookieHeader);
  const headers = forwardableHeaders(source);
  headers.set("authorization", `Bearer ${bearer}`);
  return fetch(`${base}${backendPath}`, {
    method: "POST",
    headers,
    redirect: "manual",
    cache: "no-store",
    signal: AbortSignal.timeout(timeoutMs),
  });
}
