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

/** Accepted spellings per language. Hindi has two common ones (anusvara and
 * chandrabindu) that NFC does not unify. */
const ACCEPTED_WORDS: Record<AuthLanguage, readonly string[]> = {
  en: ["DELETE"],
  hi: ["हटाएं", "हटाएँ"],
};

/** True when `typed` is an accepted confirmation word for `language`.
 * Trimmed and NFC-normalised; the English word is case-insensitive. */
export function isDeleteConfirmed(typed: unknown, language: AuthLanguage): boolean {
  if (typeof typed !== "string") return false;
  const normalise = (value: string) => value.trim().normalize("NFC");
  const given = normalise(typed);
  return ACCEPTED_WORDS[language].some((word) => {
    const expected = normalise(word);
    return language === "en" ? given.toUpperCase() === expected : given === expected;
  });
}
