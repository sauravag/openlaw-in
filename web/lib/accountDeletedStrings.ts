import type { Language } from "./languages";

/**
 * Post-deletion notice for `/?account=deleted&identity=removed|remains`
 * (rendered by Task 14). Mirrors `src/openlaw/accounts/notes.py`; the key facts
 * (what was erased, that the access log is kept without content, whether the
 * sign-in identity was removed) are checked against it by
 * `tests/accounts/test_notes_match_frontend.py`, so the two cannot drift.
 * Each string stays on ONE line so that test can find it verbatim.
 * Hindi is best-effort pending native-speaker review.
 */
export interface AccountDeletedNotice {
  readonly erased: string;
  readonly accessLogRetained: string;
  readonly identityRemoved: string;
  readonly identityRemains: string;
}

const EN: AccountDeletedNotice = {
  erased: "Your conversations, uploaded documents and excerpts have been erased, including any linked to this browser without an account.",
  accessLogRetained: "A record of who accessed each document (the access log) is kept, without any document content.",
  identityRemoved: "Your sign-in identity has also been removed.",
  identityRemains: "Your sign-in identity (your email address at our sign-in provider) could not be removed and may remain. To have it removed, contact the site operator and ask for your sign-in identity to be deleted.",
};

const HI: AccountDeletedNotice = {
  erased: "आपकी बातचीत, अपलोड किए गए दस्तावेज़ और उनके अंश मिटा दिए गए हैं, उनके साथ भी जो बिना खाते के इस ब्राउज़र से जुड़े थे।",
  accessLogRetained: "हर दस्तावेज़ को किसने देखा, इसका रिकॉर्ड (एक्सेस लॉग) बिना किसी दस्तावेज़ सामग्री के रखा गया है।",
  identityRemoved: "आपकी साइन इन पहचान भी हटा दी गई है।",
  identityRemains: "आपकी साइन इन पहचान (साइन इन प्रदाता के पास आपका ईमेल पता) हटाई नहीं जा सकी और बनी रह सकती है। इसे हटवाने के लिए साइट संचालक से संपर्क करें और अपनी साइन इन पहचान मिटाने को कहें।",
};

/** The full notice paragraph, as the backend's `note_en`/`note_hi` word it. */
export function accountDeletedNotice(
  language: Language["code"] | string,
  identityRemoved: boolean,
): string {
  const copy = language === "hi" ? HI : EN;
  const identity = identityRemoved ? copy.identityRemoved : copy.identityRemains;
  return `${copy.erased} ${copy.accessLogRetained} ${identity}`;
}
