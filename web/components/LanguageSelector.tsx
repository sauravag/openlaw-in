import { LANGUAGES, type Language } from '@/lib/languages'
import { getStrings } from '@/lib/strings'

interface LanguageSelectorProps {
  value: Language['code']
  onChange: (code: Language['code']) => void
  /** Set so the browser's native, no-JS form submission (Task 6 review, M5)
   * includes this field on its own -- without it, a submit that happens
   * before React hydrates would fall back to whatever `defaultValue` the
   * `<select>` started at, not what the user actually picked. */
  name?: string
}

/**
 * Language chooser, deliberately narrowed to English and Hindi only.
 *
 * See `lib/languages.ts` for why: Plan 4's review gated launch to the two
 * languages with a verified glossary, and offering more here would let a
 * citizen pick a language whose legal terminology has never been checked.
 * There is no "auto-detect" option either (Task 6 requirement 2) -- the
 * interface is where a user actually chooses their language, and this value
 * is always sent to `/ask` explicitly (`lib/api.ts`), never left for the
 * server to guess.
 *
 * The visible label stays bilingual in both directions ("Language / भाषा",
 * "भाषा / Language" -- `strings.languageLabel`, so it lives with every
 * other string rather than hardcoded here) rather than following the
 * selected language alone. A Hindi reader landing on the English default
 * has to be able to FIND this control, and a control labelled only in the
 * language you cannot read is the one control that must never be.
 *
 * Must stay inside the `<form>`: `name` only reaches the server on the
 * no-JS GET fallback if the field is part of that form.
 */
export default function LanguageSelector({ value, onChange, name }: LanguageSelectorProps) {
  const strings = getStrings(value)
  return (
    <label className="flex flex-col gap-1.5 text-sm font-semibold text-muted">
      {strings.languageLabel}
      <select
        name={name}
        className="min-h-12 rounded-lg border border-line bg-white px-3 py-2 text-base font-normal text-ink"
        value={value}
        onChange={(event) => onChange(event.target.value as Language['code'])}
      >
        {LANGUAGES.map((language) => (
          <option key={language.code} value={language.code}>
            {language.label}
          </option>
        ))}
      </select>
    </label>
  )
}
