import { authRefusal, normalizeLanguage, signInSentResponse, type AuthLanguage } from "@/lib/auth/authRefusal";
import { requestOtp } from "@/lib/auth/gotrue";
import { readGotrueConfig } from "@/lib/auth/gotrueConfig";
import { challengeFor, createVerifier } from "@/lib/auth/pkce";
import { clientKey, signInLimiter } from "@/lib/auth/rateLimit";
import { notFound } from "@/lib/auth/routeHelpers";
import { writeVerifier, clearVerifier } from "@/lib/auth/session";
import { authUnavailableResponse, checkAuthAgreement } from "@/lib/authAgreement";
import { AUTH_ENABLED } from "@/lib/authMode";
import { isCrossSiteWrite } from "@/lib/csrf";

// Never cache: this sets a cookie and sends mail.
export const dynamic = "force-dynamic";

const MAX_EMAIL_LENGTH = 254;
/** Largest sign-in body read; a real one is under 300 bytes. */
const MAX_BODY_BYTES = 4 * 1024;
const DEFAULT_RETRY_AFTER_SECONDS = 60;
const EMAIL_SHAPE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

interface SignInInput {
  readonly email: unknown;
  readonly language: AuthLanguage;
}

type ReadResult = { kind: "ok"; text: string } | { kind: "too_large" } | { kind: "unreadable" };

/** Reads the body, never holding more than `MAX_BODY_BYTES`: Content-Length is
 * only a hint a client can lie about, so the stream itself is bounded too. */
async function readBoundedBody(request: Request): Promise<ReadResult> {
  const declared = Number(request.headers.get("content-length"));
  if (Number.isFinite(declared) && declared > MAX_BODY_BYTES) return { kind: "too_large" };
  if (!request.body) return { kind: "unreadable" };
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > MAX_BODY_BYTES) {
        await reader.cancel();
        return { kind: "too_large" };
      }
      chunks.push(value);
    }
  } catch {
    return { kind: "unreadable" };
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return { kind: "ok", text: new TextDecoder().decode(bytes) };
}

/** `email` and `language` from JSON or the no-JavaScript form; null if unparseable. */
function parseInput(text: string, contentType: string): SignInInput | null {
  if (contentType.includes("application/x-www-form-urlencoded")) {
    const form = new URLSearchParams(text);
    return { email: form.get("email"), language: normalizeLanguage(form.get("language")) };
  }
  try {
    const data: unknown = JSON.parse(text);
    if (typeof data !== "object" || data === null) return null;
    const record = data as Record<string, unknown>;
    return { email: record.email, language: normalizeLanguage(record.language) };
  } catch {
    return null;
  }
}

/** Language for errors raised before the body is read (limiter, size cap). */
function hintLanguage(request: Request): AuthLanguage {
  const accept = request.headers.get("accept-language")?.trim().toLowerCase() ?? "";
  return normalizeLanguage(accept.startsWith("hi") ? "hi" : "en");
}

function validEmail(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const email = value.trim().toLowerCase();
  return email.length <= MAX_EMAIL_LENGTH && EMAIL_SHAPE.test(email) ? email : null;
}

/**
 * Absolute callback URL GoTrue puts in the email, from `OPENLAW_SITE_URL`
 * ONLY. It is never derived from the request origin or Host header: that
 * would let an attacker choose where the victim's magic link sends them.
 * Null when unset or not a plain http(s) URL without credentials, query or
 * fragment.
 */
function callbackUrl(): string | null {
  const raw = process.env.OPENLAW_SITE_URL?.trim();
  if (!raw) return null;
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return null;
  }
  const isHttp = url.protocol === "http:" || url.protocol === "https:";
  if (!isHttp || url.username || url.password || url.search || url.hash) return null;
  return `${url.origin}${url.pathname.replace(/\/+$/, "")}/api/auth/callback`;
}

export async function POST(request: Request): Promise<Response> {
  if (!AUTH_ENABLED) return notFound();
  // Not behind proxyToBackend, so the shared CSRF guard is applied here: a
  // cross-site page must not make a victim's browser mail magic links.
  if (isCrossSiteWrite(request)) {
    return Response.json({ detail: "Forbidden." }, { status: 403 });
  }
  // Limiter first, so a flood costs no body read.
  const hint = hintLanguage(request);
  const limit = signInLimiter.check(clientKey(request), Date.now());
  if (!limit.allowed) {
    return authRefusal("rate_limited", hint, 429, {
      "Retry-After": String(limit.retryAfterSeconds),
    });
  }
  const body = await readBoundedBody(request);
  if (body.kind === "too_large") return authRefusal("too_large", hint, 413);
  const input =
    body.kind === "ok" ? parseInput(body.text, request.headers.get("content-type") ?? "") : null;
  const language = input?.language ?? hint;
  const email = input ? validEmail(input.email) : null;
  if (email === null) return authRefusal("invalid_email", language, 400);

  // `agrees === null` (backend unreachable) does not block: GoTrue is a
  // separate service. Only a proven frontend/backend disagreement does.
  const agreement = await checkAuthAgreement();
  if (agreement.agrees === false) return authUnavailableResponse(language);

  const redirectUrl = callbackUrl();
  if (redirectUrl === null) {
    console.error("[Auth] OPENLAW_SITE_URL is missing or invalid");
    return authRefusal("failed", language, 503);
  }
  const config = readGotrueConfig();
  if (config === null) {
    console.error("[Auth] OPENLAW_SUPABASE_URL / OPENLAW_SUPABASE_ANON_KEY are not set");
    return authRefusal("failed", language, 503);
  }

  const verifier = createVerifier();
  await writeVerifier(verifier);
  let outcome;
  try {
    outcome = await requestOtp(config, {
      method: "email",
      identifier: email,
      codeChallenge: await challengeFor(verifier),
      redirectTo: redirectUrl,
    });
  } catch (error) {
    console.error("[Auth] requestOtp threw", error instanceof Error ? error.name : "unknown");
    outcome = { kind: "failed" as const, detail: "threw" };
  }

  if (outcome.kind === "sent") return signInSentResponse(language);
  await clearVerifier();
  if (outcome.kind === "rate_limited") {
    return authRefusal("rate_limited", language, 429, {
      "Retry-After": String(outcome.retryAfterSeconds ?? DEFAULT_RETRY_AFTER_SECONDS),
    });
  }
  console.error("[Auth] sign-in failed:", outcome.detail);
  return authRefusal("failed", language, 503);
}
