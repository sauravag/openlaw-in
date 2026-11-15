import "server-only";

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

function firstValue(header: string | null): string | null {
  return header?.split(",")[0].trim() || null;
}

/**
 * CSRF guard shared by every cookie-authenticated write route: the backend
 * proxy (`proxyToBackend`) and the sign-in / sign-out Route Handlers. An
 * ambient cookie becomes identity (the Bearer token in Mode B,
 * `openlaw_anon_id` in Mode A) or an action (a magic-link email, a sign-out),
 * so a cross-site page must not be able to make a browser POST/DELETE. Safe methods pass.
 * Otherwise allow when `Sec-Fetch-Site` is same-origin/none, or `Origin`'s
 * host equals the request host (`X-Forwarded-Host` first, since the app runs
 * behind nginx). If BOTH headers are absent the caller is a non-browser client
 * (curl): CSRF needs a browser, and browsers always send one of the two on a
 * cross-site POST or DELETE, so such a request is allowed.
 */
export function isCrossSiteWrite(request: Request): boolean {
  if (SAFE_METHODS.has(request.method)) return false;
  const fetchSite = request.headers.get("sec-fetch-site");
  const origin = request.headers.get("origin");
  if (fetchSite === null && origin === null) return false;
  if (fetchSite === "same-origin" || fetchSite === "none") return false;
  if (origin === null) return true;
  const host =
    firstValue(request.headers.get("x-forwarded-host")) ??
    request.headers.get("host") ??
    new URL(request.url).host;
  try {
    return new URL(origin).host !== host;
  } catch {
    return true; // `Origin: null` or garbage
  }
}
