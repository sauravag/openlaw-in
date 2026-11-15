import "server-only";

import { getFallbackHelplineSet } from "@/lib/fallbackHelplines";
import { getStrings, type UIStrings } from "@/lib/strings";
import type { RefusalData } from "@/lib/types";

/**
 * Help-carrying error bodies for the sign-in routes.
 *
 * SOURCE OF HELPLINE TEXT (decision): the bundled `fallbackHelplines.ts` set.
 * The backend exposes only `GET /auth/unavailable` (a single refusal key,
 * used by `authUnavailableResponse` for mode mismatches); it has no endpoint
 * for rate-limited / failed / invalid-input sign-in refusals, and adding one
 * means a Python change plus a backend round trip on every error path. Worse,
 * sign-in errors most often happen exactly when something is down, so the
 * footer and numbers must not depend on the backend answering. The bundled
 * set is already the single TypeScript copy and is checked bidirectionally
 * against `openlaw.api.refusal` by
 * `tests/answers/test_fallback_helpline_consistency.py`; reusing it adds no
 * third copy of the numbers. The per-error `detail` sentences live in
 * `lib/strings.ts`, pinned verbatim to `openlaw.api.refusal` by
 * `tests/api/test_auth_refusal_copy.py`.
 *
 * Hindi strings are best-effort pending native-speaker review, as elsewhere.
 */

export type AuthRefusalKind =
  | "invalid_email"
  | "rate_limited"
  | "too_large"
  | "failed"
  | "sent"
  | "confirmation_required"
  | "sign_in_required"
  | "delete_failed"
  | "delete_too_large"
  | "delete_conflict";

/** Which `UIStrings` sentence each kind speaks. The sentences live in `lib/strings.ts`. */
const DETAIL_KEY = {
  invalid_email: "authInvalidEmail",
  too_large: "authRequestTooLarge",
  rate_limited: "authRateLimited",
  failed: "authFailed",
  confirmation_required: "accountDeleteConfirmationRequired",
  sign_in_required: "accountDeleteSignInRequired",
  delete_failed: "accountDeleteFailed",
  delete_too_large: "accountDeleteTooLarge",
  delete_conflict: "accountDeleteConflict",
  sent: "authLinkSent",
} as const satisfies Record<AuthRefusalKind, keyof UIStrings>;

function detailFor(kind: AuthRefusalKind, language: AuthLanguage): string {
  return getStrings(language)[DETAIL_KEY[kind]];
}

export type AuthLanguage = "en" | "hi";

/** Only the two supported languages; anything else is English. */
export function normalizeLanguage(value: unknown): AuthLanguage {
  return value === "hi" ? "hi" : "en";
}

export function authRefusal(
  kind: Exclude<AuthRefusalKind, "sent">,
  language: AuthLanguage,
  status: number,
  headers: Record<string, string> = {},
): Response {
  const help = getFallbackHelplineSet(language);
  const body: RefusalData = {
    detail: detailFor(kind, language),
    escalation_footer: help.escalationFooter,
    crisis_response: null,
    helplines: help.helplines,
  };
  return Response.json(body, { status, headers: { "Cache-Control": "no-store", ...headers } });
}

/** The 202 body: no field depends on the address (no enumeration oracle). */
export function signInSentResponse(language: AuthLanguage): Response {
  return Response.json(
    { status: "sent", detail: detailFor("sent", language) },
    { status: 202, headers: { "Cache-Control": "no-store" } },
  );
}
