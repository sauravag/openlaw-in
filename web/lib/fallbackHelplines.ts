import type { Helpline } from "./types";

/**
 * A static, client-bundled helpline set used ONLY when the server could not
 * tell us anything at all: a network failure, a timeout/abort, or a non-2xx
 * response whose body is not recognisable `RefusalData` JSON (a proxy, CDN,
 * or load balancer's own HTML error page on a 502/503/504, an empty body,
 * or JSON that does not carry `escalation_footer`).
 *
 * Task 6 second fix round (re-review HIGH). **This is deliberately NOT the
 * same mechanism C2 removed, and the difference matters enough to spell
 * out:** C2 deleted `PhoneLinkedText`/`helplineNumbers.ts`, a client-side
 * allowlist that pattern-matched digits out of SERVER-SUPPLIED prose to
 * decide what to linkify -- a mechanism that silently produced nothing once
 * that prose was Hindi text using Devanagari numerals. This module is a
 * last-resort CONSTANT for the case where there is no server text to
 * pattern-match in the first place: the client cannot linkify text it never
 * received, so the only honest option left is to ship its own copy of the
 * same handful of national helplines the backend would have sent. Do not
 * "simplify" this by routing it back through prose-scanning, and do not
 * delete it as a duplicate of `HelplineList`'s data source -- it exists
 * precisely for the moment that data source is unreachable.
 *
 * `crisisResponse` does not claim to have answered the question -- it says
 * plainly that the server could not be reached, on the same "when in doubt,
 * show help" principle `openlaw.answers.escalation`'s module docstring
 * states for the backend's own regex layer.
 *
 * The exact numbers and labels mirror
 * `openlaw.api.refusal._GENERIC_SAFETY_NUMBERS` plus
 * `openlaw.answers.escalation.NALSA_HELPLINE`, with labels from
 * `escalation._HELPLINE_LABELS` -- checked bidirectionally against that
 * Python source by `tests/answers/test_fallback_helpline_consistency.py`.
 * A stale fallback citing a decommissioned number would be worse than
 * showing none, so that test is what makes this hand-maintained duplication
 * acceptable rather than a second, silently-drifting copy.
 *
 * Best-effort Hindi pending a native-speaker review pass, same caveat as
 * every other static Hindi string in this project.
 */
export interface FallbackHelplineSet {
  crisisResponse: string;
  escalationFooter: string;
  helplines: Helpline[];
}

const EN_HELPLINES: Helpline[] = [
  { number: "112", label: "Emergency" },
  { number: "181", label: "Women's helpline" },
  { number: "1091", label: "Police women's helpline" },
  { number: "1098", label: "Child helpline" },
  { number: "14416", label: "Tele-MANAS (mental health support)" },
  { number: "15100", label: "Free legal aid (NALSA)" },
];

const HI_HELPLINES: Helpline[] = [
  { number: "112", label: "आपातकालीन सेवा" },
  { number: "181", label: "महिला हेल्पलाइन" },
  { number: "1091", label: "पुलिस महिला हेल्पलाइन" },
  { number: "1098", label: "बाल हेल्पलाइन" },
  { number: "14416", label: "टेली-मानस (मानसिक स्वास्थ्य सहायता)" },
  { number: "15100", label: "मुफ़्त कानूनी सहायता (NALSA)" },
];

const EN: FallbackHelplineSet = {
  crisisResponse:
    "We could not reach the server just now, so your question was not checked. If you are in immediate danger, call 112. The women's helpline is 181, the police women's helpline is 1091, the child helpline is 1098, and Tele-MANAS (mental health support) is 14416. Free legal aid is available on 15100. All are free and available 24 hours.",
  escalationFooter:
    "This is legal information, not legal advice. Please try again once your connection is stable.",
  helplines: EN_HELPLINES,
};

const HI: FallbackHelplineSet = {
  crisisResponse:
    "हम अभी सर्वर से जुड़ नहीं सके, इसलिए आपके सवाल की जांच नहीं हो सकी। अगर आप तुरंत खतरे में हैं, तो 112 पर कॉल करें। महिला हेल्पलाइन 181 है, पुलिस महिला हेल्पलाइन 1091 है, बाल हेल्पलाइन 1098 है, और टेली-मानस (मानसिक स्वास्थ्य सहायता) 14416 है। मुफ़्त कानूनी सहायता 15100 पर उपलब्ध है। सभी निःशुल्क हैं और चौबीसों घंटे उपलब्ध हैं।",
  escalationFooter:
    "यह कानूनी जानकारी है, कानूनी सलाह नहीं। कृपया अपना कनेक्शन ठीक होने पर दोबारा प्रयास करें।",
  helplines: HI_HELPLINES,
};

export function getFallbackHelplineSet(language: string): FallbackHelplineSet {
  return language === "hi" ? HI : EN;
}
