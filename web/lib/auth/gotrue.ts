import "server-only";

/**
 * Minimal GoTrue (Supabase Auth) REST client.
 *
 * SERVER-ONLY. Importing this from a client component is a bug: the config it
 * takes carries the project's API key, and the sessions it returns carry
 * tokens that must never be script-readable. `import "server-only"` makes the
 * mistake a build error rather than a code-review catch.
 *
 * Deliberately no `@supabase/supabase-js`: that library keeps sessions in
 * `localStorage` by default (XSS-readable). With only `fetch` here, the token
 * stays out of the browser because the code that could put it there is not
 * installed, not because it was configured away.
 *
 * Leak discipline: GoTrue's response bodies, thrown error messages, tokens,
 * codes and verifiers are NEVER copied into an outcome or a log. `detail`
 * strings are built only from the HTTP status and a whitelisted error code,
 * so they name what to fix without revealing what the module holds. Nothing
 * here logs.
 */

export type AuthMethod = "email";

export interface GotrueConfig {
  /** e.g. `https://abc.supabase.co`. */
  readonly supabaseUrl: string;
  /** The project API key sent as `apikey`. Server-only. */
  readonly apiKey: string;
  /** Per-request timeout; defaults to `DEFAULT_TIMEOUT_MS`. */
  readonly timeoutMs?: number;
}

export interface OtpRequest {
  readonly method: AuthMethod;
  /** The email address for `method: "email"`. */
  readonly identifier: string;
  /** PKCE challenge (S256); the verifier stays server-side in an HttpOnly cookie. */
  readonly codeChallenge: string;
  /** Absolute URL of `app/api/auth/callback`; must be allow-listed in Supabase. */
  readonly redirectTo: string;
}

export type OtpOutcome =
  | { kind: "sent" }
  | { kind: "rate_limited"; retryAfterSeconds: number | null }
  | { kind: "failed"; detail: string };

export type ExchangeOutcome =
  | {
      kind: "session";
      accessToken: string;
      expiresAt: number;
      email: string;
    }
  | { kind: "expired" }
  | { kind: "failed"; detail: string };

const DEFAULT_TIMEOUT_MS = 8_000;
const MAX_BODY_CHARS = 64 * 1024;
const MAX_RETRY_AFTER_SECONDS = 24 * 60 * 60;
const HTTP_TOO_MANY_REQUESTS = 429;
const REQUIRED_AUDIENCE = "authenticated";
const SAFE_CODE = /^[a-z0-9_]{1,64}$/;
const JWT_SHAPE = /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*$/;
// GoTrue's answers for a PKCE code that is gone, used or timed out.
const EXPIRED_CODES: ReadonlySet<string> = new Set([
  "flow_state_not_found",
  "flow_state_expired",
  "otp_expired",
]);

type Transport =
  | { ok: true; status: number; retryAfter: string | null; body: unknown }
  | { ok: false; detail: string };

type Json = Record<string, unknown>;

function isRecord(value: unknown): value is Json {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function endpoint(config: GotrueConfig, path: string): string | null {
  try {
    const base = new URL(config.supabaseUrl);
    const isLocal = base.hostname === "localhost" || base.hostname === "127.0.0.1";
    if (base.protocol !== "https:" && !(isLocal && base.protocol === "http:")) {
      return null;
    }
    return new URL(path, base).toString();
  } catch {
    return null;
  }
}

async function readBody(response: Response): Promise<unknown> {
  try {
    const text = await response.text();
    if (text.length > MAX_BODY_CHARS) return null;
    return JSON.parse(text) as unknown;
  } catch {
    return null;
  }
}

/** One bounded POST. Never throws; failures carry fixed strings only. */
async function post(
  config: GotrueConfig,
  path: string,
  body: Json,
  extraHeaders: Record<string, string> = {},
): Promise<Transport> {
  const url = endpoint(config, path);
  if (url === null) return { ok: false, detail: "gotrue_url_invalid" };
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        apikey: config.apiKey,
        ...extraHeaders,
      },
      body: JSON.stringify(body),
      // Never follow a redirect: it would replay the API key to a new host.
      redirect: "error",
      cache: "no-store",
      signal: AbortSignal.timeout(config.timeoutMs ?? DEFAULT_TIMEOUT_MS),
    });
    return {
      ok: true,
      status: response.status,
      retryAfter: response.headers.get("Retry-After"),
      body: await readBody(response),
    };
  } catch (error) {
    // Compare by name: DOMException is not an `Error` in every runtime.
    const name = isRecord(error) || error instanceof Error ? (error as Json).name : null;
    const isTimeout = name === "TimeoutError";
    return { ok: false, detail: isTimeout ? "gotrue_timeout" : "gotrue_unreachable" };
  }
}

function errorCode(body: unknown): string | null {
  if (!isRecord(body)) return null;
  const code = body.error_code;
  return typeof code === "string" && SAFE_CODE.test(code) ? code : null;
}

function httpDetail(status: number, body: unknown): string {
  const code = errorCode(body);
  return code ? `gotrue_http_${status}:${code}` : `gotrue_http_${status}`;
}

function parseRetryAfter(header: string | null): number | null {
  if (header === null || !/^\d{1,9}$/.test(header.trim())) return null;
  const seconds = Number(header.trim());
  return seconds <= MAX_RETRY_AFTER_SECONDS ? seconds : null;
}

/** Start a sign-in: GoTrue emails a link carrying a PKCE `code`. */
export async function requestOtp(
  config: GotrueConfig,
  request: OtpRequest,
): Promise<OtpOutcome> {
  // Switch on `method` so a future "phone" is a new branch, not a rewrite.
  switch (request.method) {
    case "email":
      break;
    default:
      return { kind: "failed", detail: "unsupported_method" };
  }
  const query = new URLSearchParams({ redirect_to: request.redirectTo });
  const result = await post(config, `/auth/v1/otp?${query.toString()}`, {
    email: request.identifier,
    create_user: true,
    code_challenge: request.codeChallenge,
    code_challenge_method: "s256",
  });
  if (!result.ok) return { kind: "failed", detail: result.detail };
  if (result.status === HTTP_TOO_MANY_REQUESTS) {
    return { kind: "rate_limited", retryAfterSeconds: parseRetryAfter(result.retryAfter) };
  }
  if (result.status >= 200 && result.status < 300) return { kind: "sent" };
  return { kind: "failed", detail: httpDetail(result.status, result.body) };
}

function jwtClaims(token: string): Json | null {
  if (!JWT_SHAPE.test(token)) return null;
  try {
    const payload = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const parsed: unknown = JSON.parse(atob(payload));
    return isRecord(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function resolveExpiry(body: Json, claims: Json): number | null {
  for (const candidate of [body.expires_at, claims.exp]) {
    if (typeof candidate === "number" && Number.isInteger(candidate) && candidate > 0) {
      return candidate;
    }
  }
  return null;
}

/** Fail closed: every field must be present, typed and consistent with what
 * the backend verifier (`openlaw.api.auth`) requires, or there is no session. */
function parseSession(body: unknown): ExchangeOutcome {
  const invalid: ExchangeOutcome = { kind: "failed", detail: "gotrue_response_invalid" };
  if (!isRecord(body)) return invalid;
  // The refresh token is deliberately not read: token refresh is not built,
  // and a long-lived credential kept for nothing only makes a stolen cookie
  // worth more (final review L1).
  const { access_token: access, user } = body;
  if (typeof access !== "string") return invalid;
  const claims = jwtClaims(access);
  if (claims === null) return invalid;
  const hasSubject = typeof claims.sub === "string" && claims.sub.length > 0;
  const expiresAt = resolveExpiry(body, claims);
  const email = isRecord(user) && typeof user.email === "string" ? user.email.trim() : "";
  if (!hasSubject || claims.aud !== REQUIRED_AUDIENCE || expiresAt === null || !email) {
    return invalid;
  }
  return { kind: "session", accessToken: access, expiresAt, email };
}

/** Trade the emailed PKCE `code` plus our `verifier` for a session. */
export async function exchangeCode(
  config: GotrueConfig,
  code: string,
  verifier: string,
): Promise<ExchangeOutcome> {
  const result = await post(config, "/auth/v1/token?grant_type=pkce", {
    auth_code: code,
    code_verifier: verifier,
  });
  if (!result.ok) return { kind: "failed", detail: result.detail };
  if (result.status >= 200 && result.status < 300) return parseSession(result.body);
  const detailCode = errorCode(result.body);
  if (detailCode !== null && EXPIRED_CODES.has(detailCode)) return { kind: "expired" };
  return { kind: "failed", detail: httpDetail(result.status, result.body) };
}

/** Best-effort revoke of this one session; never throws, since the caller
 * clears its own cookies regardless. */
export async function revokeSession(
  config: GotrueConfig,
  accessToken: string,
): Promise<void> {
  await post(config, "/auth/v1/logout?scope=local", {}, {
    Authorization: `Bearer ${accessToken}`,
  });
}
