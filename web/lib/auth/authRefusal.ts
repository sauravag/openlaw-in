import "server-only";

import { getFallbackHelplineSet } from "@/lib/fallbackHelplines";
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
 * third copy of the numbers. Only the short per-error `detail` sentences live
 * here.
 *
 * Hindi strings are best-effort pending native-speaker review, as elsewhere.
 */

export type AuthRefusalKind =
  | "invalid_email"
  | "rate_limited"
  | "failed"
  | "sent";

const DETAIL: Record<AuthRefusalKind, Record<"en" | "hi", string>> = {
  invalid_email: {
    en: "That does not look like an email address. Please check it and try again.",
    hi: "यह ईमेल पता सही नहीं लग रहा। कृपया जांच कर दोबारा कोशिश करें।",
  },
  rate_limited: {
    en: "Too many sign-in attempts. Please wait a little and try again. You can still ask your question without an account.",
    hi: "साइन इन की बहुत ज़्यादा कोशिशें हुईं। कृपया थोड़ा रुककर दोबारा कोशिश करें। आप बिना खाते के अपना सवाल अब भी पूछ सकते हैं।",
  },
  failed: {
    en: "We could not send the sign-in email just now. Please try again later. You can still ask your question without an account.",
    hi: "हम अभी साइन इन ईमेल नहीं भेज सके। कृपया बाद में कोशिश करें। आप बिना खाते के अपना सवाल अब भी पूछ सकते हैं।",
  },
  // The one success body. Identical for every address, registered or not.
  sent: {
    en: "If that address can receive email, a sign-in link is on its way. Check your inbox.",
    hi: "अगर इस पते पर ईमेल पहुंच सकता है, तो साइन इन लिंक भेजा जा रहा है। अपना इनबॉक्स देखें।",
  },
};

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
    detail: DETAIL[kind][language],
    escalation_footer: help.escalationFooter,
    crisis_response: null,
    helplines: help.helplines,
  };
  return Response.json(body, { status, headers: { "Cache-Control": "no-store", ...headers } });
}

/** The 202 body: no field depends on the address (no enumeration oracle). */
export function signInSentResponse(language: AuthLanguage): Response {
  return Response.json(
    { status: "sent", detail: DETAIL.sent[language] },
    { status: 202, headers: { "Cache-Control": "no-store" } },
  );
}
