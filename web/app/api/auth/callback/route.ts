import { exchangeCode } from "@/lib/auth/gotrue";
import { readGotrueConfig } from "@/lib/auth/gotrueConfig";
import type { AuthLanguage } from "@/lib/auth/authRefusal";
import { signupErrorRedirect } from "@/lib/auth/authRedirects";
import { notFound, redirectTo } from "@/lib/auth/routeHelpers";
import { clearVerifier, readVerifier, writeSession } from "@/lib/auth/session";
import { checkAuthAgreement } from "@/lib/authAgreement";
import { AUTH_ENABLED } from "@/lib/authMode";
import { postToBackend } from "@/lib/backend";

export const dynamic = "force-dynamic";

const MAX_CODE_LENGTH = 512;
/** Where a completed sign-in lands, in the language the person signed in with. */
function homeRedirect(isMigrated: boolean, language: AuthLanguage): Response {
  const flags = [isMigrated ? null : "migration=failed", language === "hi" ? "language=hi" : null]
    .filter((flag) => flag !== null)
    .join("&");
  return redirectTo(flags ? `/?${flags}` : "/");
}
const MIGRATE_PATH = "/account/migrate";

/** Frees the connection. A failure here is logged and never decides the outcome. */
async function releaseBody(response: Response): Promise<void> {
  try {
    await response.body?.cancel();
  } catch (error) {
    console.error("[Auth] could not release migration response", error instanceof Error ? error.name : "unknown");
  }
}

/**
 * Moves this browser's anonymous material into the new account. Never throws
 * and never fails sign-in: any failure is logged (no tokens) and reported as
 * false so the person is told their earlier material was not moved.
 */
async function migrateAnonymousMaterial(
  accessToken: string,
  inboundCookieHeader: string | null,
): Promise<boolean> {
  try {
    const response = await postToBackend(MIGRATE_PATH, {
      bearer: accessToken,
      inboundCookieHeader,
    });
    await releaseBody(response);
    if (response.ok) return true;
    console.error("[Auth] account migration refused, status", response.status);
  } catch (error) {
    console.error("[Auth] account migration failed", error instanceof Error ? error.name : "unknown");
  }
  return false;
}

async function exchange(code: string, verifier: string) {
  const config = readGotrueConfig();
  if (config === null) {
    console.error("[Auth] OPENLAW_SUPABASE_URL / OPENLAW_SUPABASE_ANON_KEY are not set");
    return { kind: "failed" as const, detail: "config_missing" };
  }
  try {
    return await exchangeCode(config, code, verifier);
  } catch (error) {
    console.error("[Auth] exchangeCode threw", error instanceof Error ? error.name : "unknown");
    return { kind: "failed" as const, detail: "threw" };
  }
}

export async function GET(request: Request): Promise<Response> {
  if (!AUTH_ENABLED) return notFound();
  // Read before anything can fail: the language chosen at sign-in decides the
  // language of every redirect below (final review M4).
  const pending = await readVerifier();
  const language = pending?.language ?? "en";
  const agreement = await checkAuthAgreement();
  if (agreement.agrees === false) {
    await clearVerifier();
    // A navigation from an email link must land on a page, never on JSON.
    return signupErrorRedirect("unavailable", language);
  }

  const code = new URL(request.url).searchParams.get("code");
  // A missing verifier is a stale tab or a CSRF attempt: handled alike.
  if (!code || code.length > MAX_CODE_LENGTH || !pending) {
    await clearVerifier();
    return signupErrorRedirect("failed", language);
  }

  const outcome = await exchange(code, pending.verifier);
  // The verifier is single-use whatever happened, so clear it on every path.
  // (Writes only below: the cookie jar cannot be read back in this request.)
  await clearVerifier();
  if (outcome.kind === "expired") return signupErrorRedirect("expired", language);
  if (outcome.kind !== "session") {
    console.error("[Auth] code exchange failed:", outcome.detail);
    return signupErrorRedirect("failed", language);
  }
  try {
    await writeSession(outcome);
  } catch (error) {
    console.error("[Auth] could not store session", error instanceof Error ? error.message : "unknown");
    return signupErrorRedirect("failed", language);
  }
  const migrated = await migrateAnonymousMaterial(
    outcome.accessToken,
    request.headers.get("cookie"),
  );
  return homeRedirect(migrated, language);
}
