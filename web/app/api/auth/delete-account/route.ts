import { authRefusal, normalizeLanguage, type AuthLanguage } from "@/lib/auth/authRefusal";
import { readBoundedBody } from "@/lib/auth/boundedBody";
import { isDeleteConfirmed } from "@/lib/auth/deleteConfirmation";
import { revokeSession } from "@/lib/auth/gotrue";
import { readGotrueConfig } from "@/lib/auth/gotrueConfig";
import { notFound, redirectTo } from "@/lib/auth/routeHelpers";
import { clearSession, readSession } from "@/lib/auth/session";
import { AUTH_ENABLED } from "@/lib/authMode";
import { deleteToBackend } from "@/lib/backend";
import { isCrossSiteWrite } from "@/lib/csrf";

// Never cache: this destroys data and clears the session cookie.
export const dynamic = "force-dynamic";

const ACCOUNT_PATH = "/account";
const BAD_REQUEST = 400;
const UNAUTHORIZED = 401;
const PAYLOAD_TOO_LARGE = 413;
const CONFLICT = 409;
const BAD_GATEWAY = 502;
/** Challenge a bare 401 from the backend means "sign in again", not "retry". */
const BEARER_CHALLENGE = { "WWW-Authenticate": "Bearer" };

interface DeleteInput {
  readonly confirmation: unknown;
  readonly language: AuthLanguage;
}

/** `confirmation` and `language` from JSON or a form; null if unparseable. */
function parseInput(text: string, contentType: string): DeleteInput | null {
  if (contentType.includes("application/x-www-form-urlencoded")) {
    const form = new URLSearchParams(text);
    return {
      confirmation: form.get("confirmation"),
      language: normalizeLanguage(form.get("language")),
    };
  }
  try {
    const data: unknown = JSON.parse(text);
    if (typeof data !== "object" || data === null) return null;
    const record = data as Record<string, unknown>;
    return { confirmation: record.confirmation, language: normalizeLanguage(record.language) };
  } catch {
    return null;
  }
}

function hintLanguage(request: Request): AuthLanguage {
  const accept = request.headers.get("accept-language")?.trim().toLowerCase() ?? "";
  return accept.startsWith("hi") ? "hi" : "en";
}

/** Whether the backend confirmed the sign-in identity is gone. Anything
 * unparseable counts as NOT removed: never claim what was not confirmed. */
async function identityRemoved(response: Response): Promise<boolean> {
  try {
    const body: unknown = await response.json();
    return (body as { identity_removed?: unknown } | null)?.identity_removed === true;
  } catch {
    return false;
  }
}

/** Best-effort, like sign-out: the cookie is cleared regardless. The identity
 * may already be gone at GoTrue, in which case this simply fails quietly. */
async function revokeSessionBestEffort(accessToken: string): Promise<void> {
  const config = readGotrueConfig();
  if (config === null) return;
  try {
    await revokeSession(config, accessToken);
  } catch (error) {
    console.error("[Auth] revokeSession threw", error instanceof Error ? error.name : "unknown");
  }
}

export async function POST(request: Request): Promise<Response> {
  if (!AUTH_ENABLED) return notFound();
  if (isCrossSiteWrite(request)) {
    return Response.json({ detail: "Forbidden." }, { status: 403 });
  }
  const hint = hintLanguage(request);
  const body = await readBoundedBody(request);
  if (body.kind === "too_large") return authRefusal("delete_too_large", hint, PAYLOAD_TOO_LARGE);
  const input =
    body.kind === "ok" ? parseInput(body.text, request.headers.get("content-type") ?? "") : null;
  const language = input?.language ?? hint;
  // Before the session or the backend: a wrong word must cost nothing.
  if (input === null || !isDeleteConfirmed(input.confirmation, language)) {
    return authRefusal("confirmation_required", language, BAD_REQUEST);
  }

  const session = await readSession();
  if (!session) {
    return authRefusal("sign_in_required", language, UNAUTHORIZED, BEARER_CHALLENGE);
  }

  let upstream: Response;
  try {
    upstream = await deleteToBackend(ACCOUNT_PATH, {
      bearer: session.accessToken,
      inboundCookieHeader: request.headers.get("cookie"),
    });
  } catch (error) {
    console.error("[Auth] account deletion call failed", error instanceof Error ? error.name : "unknown");
    return authRefusal("delete_failed", language, BAD_GATEWAY);
  }
  if (upstream.status === UNAUTHORIZED) {
    return authRefusal("sign_in_required", language, UNAUTHORIZED, BEARER_CHALLENGE);
  }
  if (upstream.status === CONFLICT) {
    return authRefusal("delete_conflict", language, CONFLICT);
  }
  if (!upstream.ok) {
    // The session stays: the person must be able to retry.
    console.error("[Auth] account deletion refused, status", upstream.status);
    return authRefusal("delete_failed", language, BAD_GATEWAY);
  }

  const removed = await identityRemoved(upstream);
  await revokeSessionBestEffort(session.accessToken);
  await clearSession();
  return redirectTo(`/?account=deleted&identity=${removed ? "removed" : "remains"}`);
}
