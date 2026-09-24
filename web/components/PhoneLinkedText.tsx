import { splitOnHelplineNumbers } from "@/lib/helplineNumbers";

interface PhoneLinkedTextProps {
  text: string;
}

/**
 * Renders `text` with every recognised helpline number as a `tel:` link.
 *
 * On the phone this product's users actually carry, a plain printed number
 * still takes several steps (read it, switch to the dialler, type it in) at
 * the exact moment they may be in danger. A `tel:` link turns that into one
 * tap. See `lib/helplineNumbers.ts` for why only a known, fixed set of
 * numbers is linkified rather than any run of digits.
 */
export default function PhoneLinkedText({ text }: PhoneLinkedTextProps) {
  return (
    <>
      {splitOnHelplineNumbers(text).map((segment, index) =>
        segment.isPhoneNumber ? (
          <a
            key={index}
            href={`tel:${segment.text}`}
            className="font-bold underline underline-offset-2"
          >
            {segment.text}
          </a>
        ) : (
          <span key={index}>{segment.text}</span>
        ),
      )}
    </>
  );
}
