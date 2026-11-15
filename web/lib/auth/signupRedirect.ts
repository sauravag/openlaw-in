import "server-only";

import type { AuthLanguage } from "@/lib/auth/authRefusal";
import { redirectTo } from "@/lib/auth/routeHelpers";

/**
 * The fixed set of `/signup?error=` codes. `app/signup/page.tsx` maps each to
 * its localised refusal copy; anything else renders the generic "failed"
 * copy, and the code itself is never echoed into the page.
 */
export type SignupErrorCode =
  | "rate_limited"
  | "failed"
  | "invalid_email"
  | "too_large"
  | "unavailable";

function withLanguage(query: string, language: AuthLanguage): string {
  return language === "hi" ? `${query}&language=hi` : query;
}

/** 303 to the "check your email" state. Identical for every address. */
export function signupSentRedirect(language: AuthLanguage): Response {
  return redirectTo(`/signup${withLanguage("?sent=1", language)}`);
}

/** 303 to the localised refusal for `code`. */
export function signupErrorRedirect(code: SignupErrorCode, language: AuthLanguage): Response {
  return redirectTo(`/signup${withLanguage(`?error=${code}`, language)}`);
}
