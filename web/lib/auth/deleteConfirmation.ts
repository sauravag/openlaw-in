import type { AuthLanguage } from "@/lib/auth/authRefusal";

/**
 * The word a person must type to delete their account, per language. A bare
 * "Are you sure?" button is satisfied by a mis-tap; typing a word is not.
 * Checked SERVER-SIDE in `app/api/auth/delete-account/route.ts` (the UI may
 * show these, but never decides). Hindi is best-effort pending native review.
 */
export const DELETE_CONFIRMATION_WORD: Record<AuthLanguage, string> = {
  en: "DELETE",
  hi: "हटाएं",
};

/** True when `typed` is the confirmation word for `language`. Trimmed; the
 * English word is case-insensitive, Hindi is compared NFC-normalised. */
export function isDeleteConfirmed(typed: unknown, language: AuthLanguage): boolean {
  if (typeof typed !== "string") return false;
  const normalise = (value: string) => value.trim().normalize("NFC");
  const given = normalise(typed);
  const expected = normalise(DELETE_CONFIRMATION_WORD[language]);
  return language === "en"
    ? given.toUpperCase() === expected
    : given === expected;
}
