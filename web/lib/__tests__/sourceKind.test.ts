import { describe, expect, it } from 'vitest'
import { classifySource, sourceHost } from '../sourceKind'
import type { SourceCard } from '../types'

function card(overrides: Partial<SourceCard> = {}): SourceCard {
  return {
    chunk_id: 'a',
    source_url: 'https://www.indiacode.nic.in/handle/123456789/2385',
    kind: 'provision',
    title: 'Model Tenancy Act',
    case_id: null,
    court_code: null,
    decision_date: null,
    judges: [],
    headnote: null,
    ...overrides,
  }
}

describe('sourceHost', () => {
  it('returns the bare hostname so the reader can see where a link goes', () => {
    expect(sourceHost('https://www.indiacode.nic.in/handle/123456789/1')).toBe(
      'indiacode.nic.in',
    )
  })

  it('returns null for a value that is not a URL at all', () => {
    expect(sourceHost('not a url')).toBeNull()
  })
})

describe('classifySource', () => {
  it('reads the kind the server sent instead of guessing from the URL', () => {
    expect(classifySource(card({ kind: 'provision' }))).toBe('statute')
    expect(classifySource(card({ kind: 'judgment' }))).toBe('judgment')
  })

  it('does not reclassify a judgment by its hostname', () => {
    // A judgment served from India Code, or a statute served from anywhere
    // else, must keep the kind the corpus recorded. The old hostname
    // heuristic got both of these wrong.
    expect(
      classifySource(
        card({ kind: 'judgment', source_url: 'https://indiacode.nic.in/x', title: null }),
      ),
    ).toBe('judgment')
    expect(
      classifySource(card({ kind: 'provision', source_url: 'https://egazette.gov.in/1' })),
    ).toBe('statute')
  })

  it('falls back to the neutral kind for a value it does not recognise', () => {
    // Defence in depth at the boundary: an unknown kind is labelled with the
    // honest "we cannot tell" word rather than guessed at, and never
    // mislabelled as an Act or a judgment.
    expect(classifySource(card({ kind: 'something-new' as SourceCard['kind'] }))).toBe(
      'document',
    )
  })
})
