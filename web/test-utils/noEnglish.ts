import { expect } from 'vitest'
import { getStrings } from '@/lib/strings'

/**
 * Asserts that no plain-sentence English UI string appears in `text`. Used on
 * Hindi renders: a component that falls back to English for any one label
 * trips it. The Hindi dictionary entry must be present for the same key, so a
 * missing translation cannot pass by rendering nothing.
 */
export function expectNoEnglishStrings(text: string, usedKeys?: readonly string[]): void {
  const en = getStrings('en') as unknown as Record<string, unknown>
  const hi = getStrings('hi') as unknown as Record<string, unknown>
  for (const [key, value] of Object.entries(en)) {
    if (typeof value !== 'string') continue
    if (usedKeys && !usedKeys.includes(key)) continue
    expect(text, `English string for "${key}" leaked`).not.toContain(value)
    expect(hi[key], key).not.toBe(value)
  }
}
