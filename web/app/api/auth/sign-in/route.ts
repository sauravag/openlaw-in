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
const DEFAULT_RETRY_AFTER_SECONDS = 60;
const EMAIL_SHAPE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

interface SignInInput {
  readonly email: unknown;
  readonly language: AuthLanguage;
}

/** `email` and `language` from JSON or the no-JavaScript form; null if unparseable. */
async function parseInput(request: Request): Promise<SignInInput | null> {
  const type = request.headers.get("content-type") ?? "";
  try {
    if (type.includes("application/x-www-form-urlencoded")) {
      const form = await request.formData();
      return { email: form.get("email"), language: normalizeLanguage(form.get("language")) };
    }
    const data: unknown = await request.json();
    if (typeof data !== "object" || data === null) return null;
    const record = data as Record<string, unknown>;
    return { email: record.email, language: normalizeLanguage(record.language) };
  } catch {
    return null;
  }
}

function validEmail(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const email = value.trim().toLowerCase();
  return email.length <= MAX_EMAIL_LENGTH && EMAIL_SHAPE.test(email) ? email : null;
}

/** Absolute callback URL GoTrue puts in the email. `OPENLAW_SITE_URL` wins so a
 * spoofed Host header cannot steer it; Supabase's redirect allow-list is the
 * second line of defence. */
function callbackUrl(request: Request): string {
  const base = process.env.OPENLAW_SITE_URL?.trim().replace(/\/+$/, "");
  return `${base || new URL(request.url).origin}/api/auth/callback`;
}

export async function POST(request: Request): Promise<Response> {
  if (!AUTH_ENABLED) return notFound();
  // Not behind proxyToBackend, so the shared CSRF guard is applied here: a
  // cross-site page must not make a victim's browser mail magic links.
  if (isCrossSiteWrite(request)) {
    return Response.json({ detail: "Forbidden." }, { status: 403 });
  }
  const input = await parseInput(request);
  const language = input?.language ?? "en";

  const limit = signInLimiter.check(clientKey(request), Date.now());
  if (!limit.allowed) {
    return authRefusal("rate_limited", language, 429, {
      "Retry-After": String(limit.retryAfterSeconds),
    });
  }
  const email = input ? validEmail(input.email) : null;
  if (email === null) return authRefusal("invalid_email", language, 400);

  // `agrees === null` (backend unreachable) does not block: GoTrue is a
  // separate service. Only a proven frontend/backend disagreement does.
  const agreement = await checkAuthAgreement();
  if (agreement.agrees === false) return authUnavailableResponse(language);

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
      redirectTo: callbackUrl(request),
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
