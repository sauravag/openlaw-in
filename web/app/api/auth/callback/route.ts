import { exchangeCode } from "@/lib/auth/gotrue";
import { readGotrueConfig } from "@/lib/auth/gotrueConfig";
import { notFound, redirectTo } from "@/lib/auth/routeHelpers";
import { clearVerifier, readVerifier, writeSession } from "@/lib/auth/session";
import { authUnavailableResponse, checkAuthAgreement } from "@/lib/authAgreement";
import { AUTH_ENABLED } from "@/lib/authMode";

export const dynamic = "force-dynamic";

const MAX_CODE_LENGTH = 512;
const FAILED = "/signup?error=failed";
const EXPIRED = "/signup?error=expired";

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
  // The callback has no language of its own; the mismatch body is English.
  const agreement = await checkAuthAgreement();
  if (agreement.agrees === false) {
    await clearVerifier();
    return authUnavailableResponse("en");
  }

  const code = new URL(request.url).searchParams.get("code");
  const verifier = await readVerifier();
  // A missing verifier is a stale tab or a CSRF attempt: handled alike.
  if (!code || code.length > MAX_CODE_LENGTH || !verifier) {
    await clearVerifier();
    return redirectTo(FAILED);
  }

  const outcome = await exchange(code, verifier);
  // The verifier is single-use whatever happened, so clear it on every path.
  // (Writes only below: the cookie jar cannot be read back in this request.)
  await clearVerifier();
  if (outcome.kind === "expired") return redirectTo(EXPIRED);
  if (outcome.kind !== "session") {
    console.error("[Auth] code exchange failed:", outcome.detail);
    return redirectTo(FAILED);
  }
  try {
    await writeSession(outcome);
  } catch (error) {
    console.error("[Auth] could not store session", error instanceof Error ? error.message : "unknown");
    return redirectTo(FAILED);
  }
  // Task 10: call the backend's POST /account/migrate here with
  // `Authorization: Bearer ${outcome.accessToken}` to adopt anonymous history.
  return redirectTo("/");
}
