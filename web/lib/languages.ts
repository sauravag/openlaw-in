/** A language this interface may ask the API to answer in. */
export interface Language {
  code: 'en' | 'hi'
  label: string
}

/**
 * Only English and Hindi are offered here.
 *
 * `openlaw.language.detect.SUPPORTED_LANGUAGES` recognises twelve languages,
 * but Plan 4's review gated launch to English and Hindi: Hindi has all
 * twelve glossary terms verified by a native speaker, and the other ten
 * (Bengali, Tamil, Telugu, Marathi, Gujarati, Kannada, Malayalam, Punjabi,
 * Odia, Assamese) have none. Offering a language whose legal terminology is
 * unverified would let a citizen rely on a mistranslated statutory term --
 * this list is the interface's enforcement of that gate, not an oversight.
 * Widen it only alongside a corresponding glossary review, never alone.
 */
export const LANGUAGES: readonly Language[] = [
  { code: 'en', label: 'English' },
  { code: 'hi', label: 'हिन्दी (Hindi)' },
]

export const DEFAULT_LANGUAGE: Language['code'] = 'en'
