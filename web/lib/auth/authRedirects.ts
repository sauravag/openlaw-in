import "server-only";

import type { AuthLanguage } from "@/lib/auth/authRefusal";
import { redirectTo } from "@/lib/auth/routeHelpers";

/**
 * The fixed sets of `?error=` codes the no-JavaScript auth forms redirect to.
 * `app/signup/page.tsx` and `app/account/delete/page.tsx` map each to its
 * localised copy; anything else renders generic "failed" copy and is never
 * echoed. Every redirect is a relative path built from these closed unions
 * plus a normalised language, so nothing from the request reaches `Location`.
 */
export type SignupErrorCode =
  | "rate_limited"
  | "failed"
  | "invalid_email"
  | "too_large"
  | "unavailable"
  | "expired";

export type AccountDeleteErrorCode = "confirmation_required" | "failed" | "too_large" | "conflict";

function withLanguage(path: string, language: AuthLanguage): string {
  const separator = path.includes("?") ? "&" : "?";
  return language === "hi" ? `${path}${separator}language=hi` : path;
}

/** 303 to the bare sign-in page (e.g. the session is gone). */
export function signupRedirect(language: AuthLanguage): Response {
  return redirectTo(withLanguage("/signup", language));
}

/** 303 to the "check your email" state. Identical for every address. */
export function signupSentRedirect(language: AuthLanguage): Response {
  return redirectTo(withLanguage("/signup?sent=1", language));
}

/** 303 to the localised refusal for `code` on the sign-in page. */
export function signupErrorRedirect(code: SignupErrorCode, language: AuthLanguage = "en"): Response {
  return redirectTo(withLanguage(`/signup?error=${code}`, language));
}

/** 303 to the localised refusal for `code` on the delete-account page. */
export function accountDeleteErrorRedirect(
  code: AccountDeleteErrorCode,
  language: AuthLanguage,
): Response {
  return redirectTo(withLanguage(`/account/delete?error=${code}`, language));
}
