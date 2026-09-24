/**
 * The exact helpline numbers `openlaw.answers.escalation` and
 * `openlaw.api.refusal` ever embed in user-facing text (`crisis_response`,
 * `GENERIC_SAFETY_NOTE`, `escalation_footer`'s `NALSA_HELPLINE`).
 *
 * Matched by exact value, not "any run of 3+ digits": a naive digit regex
 * would also treat "1987" in "the Legal Services Authorities Act, 1987"
 * (`escalation_footer`'s own text) as if it were a phone number.
 */
export const KNOWN_HELPLINE_NUMBERS: readonly string[] = [
  "112", // all-purpose emergency
  "181", // women's helpline
  "1091", // police women's helpline
  "1098", // child helpline
  "14416", // Tele-MANAS (mental health)
  "14433", // National Human Rights Commission
  "15100", // NALSA free legal aid
];

const HELPLINE_PATTERN = new RegExp(
  `\\b(${KNOWN_HELPLINE_NUMBERS.join("|")})\\b`,
  "g",
);

export interface TextSegment {
  text: string;
  isPhoneNumber: boolean;
}

/**
 * Split `text` into segments, flagging the ones that are a known helpline
 * number so a caller can render them as tap-to-call `tel:` links.
 *
 * On the cheap Android phone this product is designed for, a plain printed
 * number still costs several steps (read it, switch to the dialler, type it
 * in) at the exact moment someone may be in danger. This is the one function
 * every helpline-rendering component (`PhoneLinkedText`) routes through, so
 * "which numbers count as a helpline" is defined once, not per call site.
 */
export function splitOnHelplineNumbers(text: string): TextSegment[] {
  const segments: TextSegment[] = [];
  let lastIndex = 0;
  for (const match of text.matchAll(HELPLINE_PATTERN)) {
    const index = match.index ?? 0;
    if (index > lastIndex) {
      segments.push({ text: text.slice(lastIndex, index), isPhoneNumber: false });
    }
    segments.push({ text: match[0], isPhoneNumber: true });
    lastIndex = index + match[0].length;
  }
  if (lastIndex < text.length) {
    segments.push({ text: text.slice(lastIndex), isPhoneNumber: false });
  }
  return segments;
}
