import { describe, expect, it } from 'vitest'
import { formatConversationTime } from '@/lib/conversationTime'

// Local-time constructors keep the tests independent of the machine's timezone.
const NOW = new Date(2026, 10, 16, 12, 0)
const iso = (...parts: [number, number, number, number?, number?]): string =>
  new Date(...parts).toISOString()

describe('formatConversationTime', () => {
  it('says Today with a 24-hour time for the same local day', () => {
    expect(formatConversationTime(iso(2026, 10, 16, 9, 3), 'en', NOW)).toBe('Today, 9:03')
  })

  it('says Yesterday with a 24-hour time for the previous local day', () => {
    expect(formatConversationTime(iso(2026, 10, 15, 18, 40), 'en', NOW)).toBe('Yesterday, 18:40')
  })

  it('shows day and month for an earlier date this year', () => {
    expect(formatConversationTime(iso(2026, 10, 12, 8, 0), 'en', NOW)).toBe('12 Nov')
  })

  it('adds the year for a date in another year', () => {
    expect(formatConversationTime(iso(2025, 10, 12, 8, 0), 'en', NOW)).toBe('12 Nov 2025')
  })

  it('uses the Hindi word for today', () => {
    expect(formatConversationTime(iso(2026, 10, 16, 9, 3), 'hi', NOW)).toMatch(/^आज, /)
  })

  it('uses the Hindi word for yesterday', () => {
    expect(formatConversationTime(iso(2026, 10, 15, 18, 40), 'hi', NOW)).toMatch(/^कल, /)
  })

  it('returns an invalid timestamp unchanged', () => {
    expect(formatConversationTime('not-a-date', 'en', NOW)).toBe('not-a-date')
  })
})
