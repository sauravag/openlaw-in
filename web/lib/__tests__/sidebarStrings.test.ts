import { describe, expect, it } from 'vitest'
import { getSidebarStrings } from '../sidebarStrings'
import { getStrings } from '../strings'

describe('sidebar strings (Mode B only)', () => {
  it('has the same keys in English and Hindi, all filled', () => {
    const en = getSidebarStrings('en')
    const hi = getSidebarStrings('hi')
    expect(Object.keys(hi).sort()).toEqual(Object.keys(en).sort())
    for (const value of Object.values(hi)) {
      if (typeof value === 'string') expect(value.trim()).not.toBe('')
    }
    expect(hi.newChatButton).not.toBe(en.newChatButton)
  })

  it('falls back to English for an unknown language', () => {
    expect(getSidebarStrings('xx')).toBe(getSidebarStrings('en'))
  })

  it('is not in the shared strings table every page bundles', () => {
    const shared = Object.keys(getStrings('en'))
    for (const key of Object.keys(getSidebarStrings('en'))) expect(shared).not.toContain(key)
  })

  it('counts documents, and says nothing for none', () => {
    const en = getSidebarStrings('en')
    expect(en.conversationDocuments(0)).toBe('')
    expect(en.conversationDocuments(1)).toBe('1 document')
    expect(en.conversationDocuments(3)).toBe('3 documents')
    expect(getSidebarStrings('hi').conversationDocuments(2)).toBe('2 दस्तावेज़')
  })
})
