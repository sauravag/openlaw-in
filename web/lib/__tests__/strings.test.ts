import { describe, expect, it } from 'vitest'
import { getStrings } from '@/lib/strings'

/**
 * The enforcement that Hindi is never an afterthought.
 *
 * `UIStrings` already makes a key added to `EN` alone a compile error, which
 * catches a *missing* Hindi key. It cannot catch the other half: a Hindi
 * value copied verbatim from English, which type-checks perfectly and ships
 * an English sentence to a Hindi reader. Task 17 added a dozen strings at
 * once (documents, uploads, deletion), so both halves are asserted here.
 */
describe('UI strings', () => {
  const en = getStrings('en')
  const hi = getStrings('hi')

  it('exposes exactly the same keys in both dictionaries', () => {
    expect(Object.keys(hi).sort()).toEqual(Object.keys(en).sort())
  })

  it('translates every plain string rather than reusing the English one', () => {
    const untranslated = Object.keys(en).filter((key) => {
      const english = en[key as keyof typeof en]
      const hindi = hi[key as keyof typeof hi]
      return typeof english === 'string' && english === hindi
    })
    expect(untranslated).toEqual([])
  })

  it('translates every list of strings rather than reusing the English one', () => {
    const untranslated = Object.keys(en).filter((key) => {
      const english = en[key as keyof typeof en]
      const hindi = hi[key as keyof typeof hi]
      if (!Array.isArray(english) || !Array.isArray(hindi)) return false
      return english.some((item, index) => item === hindi[index])
    })
    expect(untranslated).toEqual([])
  })

  it('carries the document-versus-law wording Task 17 depends on, in both languages', () => {
    // These two strings are the whole visual contract of the document
    // section: if either is ever blanked out, a quote from someone's lease
    // can be read as a statement of law.
    expect(en.documentCitationsHeading).toBe('From your document')
    expect(en.documentNotLawNote).toBe('This is your own document, not law.')
    expect(hi.documentCitationsHeading).toBe('आपके दस्तावेज़ से')
    expect(hi.documentNotLawNote).toBe('यह आपका अपना दस्तावेज़ है, कानून नहीं।')
  })

  it('names the file being deleted in the delete control, in both languages', () => {
    expect(en.deleteDocumentLabel('lease.pdf')).toContain('lease.pdf')
    expect(hi.deleteDocumentLabel('lease.pdf')).toContain('lease.pdf')
    expect(en.deleteDocumentLabel('lease.pdf')).not.toBe(
      hi.deleteDocumentLabel('lease.pdf'),
    )
  })

  it('states the anonymous retention window and the one action that changes it', () => {
    expect(en.anonymousUploadNotice(24)).toBe(
      'This document will be deleted after 24 hours. Sign in to keep it.',
    )
    expect(hi.anonymousUploadNotice(24)).toContain('24')
  })
})
