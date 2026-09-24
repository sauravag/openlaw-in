import { LANGUAGES, type Language } from '@/lib/languages'

interface LanguageSelectorProps {
  value: Language['code']
  onChange: (code: Language['code']) => void
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
 */
export default function LanguageSelector({ value, onChange }: LanguageSelectorProps) {
  return (
    <label className="flex flex-col gap-1 text-sm font-medium text-slate-700">
      Language / भाषा
      <select
        className="rounded-md border border-slate-300 px-3 py-2 text-base text-slate-900"
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
