import { authRefusal, type AuthLanguage, type AuthRefusalKind } from "@/lib/auth/authRefusal";
import { readBoundedBody } from "@/lib/auth/boundedBody";
import { hintLanguage, parseFormInput } from "@/lib/auth/formInput";
import { notFound } from "@/lib/auth/routeHelpers";
import { readSession } from "@/lib/auth/session";
import { AUTH_ENABLED } from "@/lib/authMode";
import { postToBackend } from "@/lib/backend";
import { isCrossSiteWrite } from "@/lib/csrf";

// Never cache: this moves one person's material between owners.
export const dynamic = "force-dynamic";

const MIGRATE_PATH = "/account/migrate";
const FORBIDDEN = 403;
const UNAUTHORIZED = 401;
const CONFLICT = 409;
const BAD_GATEWAY = 502;
const BEARER_CHALLENGE = { "WWW-Authenticate": "Bearer" };

type MigrationRefusal = Extract<
  AuthRefusalKind,
  "migration_sign_in_required" | "migration_failed" | "migration_email_conflict"
>;

/** The backend's refusal status, as the refusal the page shows. */
function refusalFor(status: number): [MigrationRefusal, number] {
  if (status === UNAUTHORIZED) return ["migration_sign_in_required", UNAUTHORIZED];
  if (status === CONFLICT) return ["migration_email_conflict", CONFLICT];
  return ["migration_failed", BAD_GATEWAY];
}

async function requestLanguage(request: Request): Promise<AuthLanguage> {
  const body = await readBoundedBody(request);
  const input =
    body.kind === "ok" ? parseFormInput(body.text, request.headers.get("content-type") ?? "", []) : null;
  return input?.language ?? hintLanguage(request);
}

/** Counts from the backend's 200, or zeros when the body is unreadable: the
 * move itself was confirmed by the status. */
async function movedCounts(response: Response): Promise<{ conversations_moved: number; documents_moved: number }> {
  try {
    const body = (await response.json()) as Record<string, unknown>;
    const count = (value: unknown) => (typeof value === "number" ? value : 0);
    return { conversations_moved: count(body.conversations_moved), documents_moved: count(body.documents_moved) };
  } catch {
    return { conversations_moved: 0, documents_moved: 0 };
  }
}

/**
 * Retry the anonymous-to-account migration (final review H2). The callback
 * migrates once at sign-in; when that failed, the page offers "Try again",
 * which posts here. Same CSRF rule as every other state-changing route;
 * the bearer comes from the session cookie, read server-side, and only the
 * inbound `openlaw_anon_id` travels upstream (`postToBackend`'s allowlist).
 * Every refusal carries the escalation footer and helplines.
 */
export async function POST(request: Request): Promise<Response> {
  if (!AUTH_ENABLED) return notFound();
  if (isCrossSiteWrite(request)) {
    return authRefusal("migration_failed", hintLanguage(request), FORBIDDEN);
  }
  const language = await requestLanguage(request);
  const session = await readSession();
  if (!session) {
    return authRefusal("migration_sign_in_required", language, UNAUTHORIZED, BEARER_CHALLENGE);
  }
  let upstream: Response;
  try {
    upstream = await postToBackend(MIGRATE_PATH, {
      bearer: session.accessToken,
      inboundCookieHeader: request.headers.get("cookie"),
    });
  } catch (error) {
    console.error("[Auth] migration retry failed", error instanceof Error ? error.name : "unknown");
    return authRefusal("migration_failed", language, BAD_GATEWAY);
  }
  if (!upstream.ok) {
    console.error("[Auth] migration retry refused, status", upstream.status);
    const [kind, status] = refusalFor(upstream.status);
    return authRefusal(kind, language, status, status === UNAUTHORIZED ? BEARER_CHALLENGE : {});
  }
  return Response.json(
    { status: "migrated", ...(await movedCounts(upstream)) },
    { headers: { "Cache-Control": "no-store" } },
  );
}
