/**
 * Format an ISO date string (`quota_reset_at`, e.g. `"2026-10-01"`) for
 * display, in the given language's Indian locale.
 *
 * Task 6 review, L2: rendering the raw ISO string under an English "Resets
 * on" label read as a developer artefact, not something written for the
 * person seeing it. Falls back to the raw string on a malformed date rather
 * than throwing -- this renders inside a refusal notice, the last place a
 * thrown error should originate.
 */
export function formatResetDate(isoDate: string, language: string): string {
  const parsed = new Date(`${isoDate}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) {
    return isoDate;
  }
  const locale = language === "hi" ? "hi-IN" : "en-IN";
  return new Intl.DateTimeFormat(locale, {
    day: "numeric",
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  }).format(parsed);
}
