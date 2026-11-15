import { authRefusal, type AuthLanguage, type AuthRefusalKind } from "@/lib/auth/authRefusal";
import { readBoundedBody } from "@/lib/auth/boundedBody";
import { isDeleteConfirmed } from "@/lib/auth/deleteConfirmation";
import { hintLanguage, isFormPost, parseFormInput } from "@/lib/auth/formInput";
import { revokeSession } from "@/lib/auth/gotrue";
import { readGotrueConfig } from "@/lib/auth/gotrueConfig";
import { accountDeleteErrorRedirect, signupRedirect, type AccountDeleteErrorCode } from "@/lib/auth/authRedirects";
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

type Refuse = (
  kind: DeleteRefusalKind,
  language: AuthLanguage,
  status: number,
  headers?: Record<string, string>,
) => Response;

type DeleteRefusalKind = Exclude<AuthRefusalKind, "sent" | "invalid_email" | "rate_limited" | "failed" | "too_large">;

/** The `/account/delete?error=` code a refusal kind lands on for a form post. */
const FORM_CODE: Record<DeleteRefusalKind, AccountDeleteErrorCode | null> = {
  confirmation_required: "confirmation_required",
  sign_in_required: null, // goes to /signup, not an error on this page
  delete_failed: "failed",
  delete_too_large: "too_large",
  delete_conflict: "conflict",
};

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
  const isForm = isFormPost(request);
  const hint = hintLanguage(request);
  // A form post must land on a page, never on a JSON body.
  const refuse: Refuse = isForm
    ? (kind, language) => {
        const code = FORM_CODE[kind];
        return code === null ? signupRedirect(language) : accountDeleteErrorRedirect(code, language);
      }
    : (kind, language, status, headers) => authRefusal(kind, language, status, headers);
  if (isCrossSiteWrite(request)) {
    return isForm
      ? accountDeleteErrorRedirect("failed", hint)
      : Response.json({ detail: "Forbidden." }, { status: 403 });
  }
  const body = await readBoundedBody(request);
  if (body.kind === "too_large") return refuse("delete_too_large", hint, PAYLOAD_TOO_LARGE);
  const input =
    body.kind === "ok"
      ? parseFormInput(body.text, request.headers.get("content-type") ?? "", ["confirmation"])
      : null;
  const language = input?.language ?? hint;
  // Before the session or the backend: a wrong word must cost nothing.
  if (input === null || !isDeleteConfirmed(input.fields.confirmation, language)) {
    return refuse("confirmation_required", language, BAD_REQUEST);
  }

  const session = await readSession();
  if (!session) {
    return refuse("sign_in_required", language, UNAUTHORIZED, BEARER_CHALLENGE);
  }

  let upstream: Response;
  try {
    upstream = await deleteToBackend(ACCOUNT_PATH, {
      bearer: session.accessToken,
      inboundCookieHeader: request.headers.get("cookie"),
    });
  } catch (error) {
    console.error("[Auth] account deletion call failed", error instanceof Error ? error.name : "unknown");
    return refuse("delete_failed", language, BAD_GATEWAY);
  }
  if (upstream.status === UNAUTHORIZED) {
    return refuse("sign_in_required", language, UNAUTHORIZED, BEARER_CHALLENGE);
  }
  if (upstream.status === CONFLICT) {
    return refuse("delete_conflict", language, CONFLICT);
  }
  if (!upstream.ok) {
    // The session stays: the person must be able to retry.
    console.error("[Auth] account deletion refused, status", upstream.status);
    return refuse("delete_failed", language, BAD_GATEWAY);
  }

  const removed = await identityRemoved(upstream);
  await revokeSessionBestEffort(session.accessToken);
  await clearSession();
  return redirectTo(`/?account=deleted&identity=${removed ? "removed" : "remains"}`);
}
