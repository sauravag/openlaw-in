import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import SourceList from '../SourceList'
import type { SourceCard } from '@/lib/types'

function card(overrides: Partial<SourceCard> = {}): SourceCard {
  return {
    chunk_id: 'c',
    source_url: 'https://www.indiacode.nic.in/handle/123456789/2385',
    kind: 'provision',
    title: null,
    case_id: null,
    court_code: null,
    decision_date: null,
    judges: [],
    headnote: null,
    ...overrides,
  }
}

const statute: SourceCard = card({
  chunk_id: 'statute-1',
  title: 'Model Tenancy Act',
})

// The real shape of a Supreme Court citation: the judgment payload has no
// `title` key, so `title` is null and the URL is a tarball nobody can read --
// but since Plan 6 Task 7 the response carries the case id and decision date
// the corpus recorded, so the card no longer has to fall back to a bare kind.
const judgment: SourceCard = card({
  chunk_id: 'judgment-1',
  source_url:
    'https://indian-supreme-court-judgments.s3.amazonaws.com/data/tar/1963_INSC_244.tar',
  kind: 'judgment',
  case_id: '1963_INSC_244',
  decision_date: '1963-04-05',
})

describe('SourceList', () => {
  it('names a judgment by the case id the response actually carries', () => {
    render(<SourceList sources={[judgment]} language="en" />)

    const link = screen.getByRole('link', { name: /1963_INSC_244/ })
    expect(link).toHaveAttribute('href', judgment.source_url)
    // The old UI printed the whole S3 path as the link text.
    expect(screen.queryByText(/s3\.amazonaws\.com\/data\/tar/)).not.toBeInTheDocument()
  })

  it('shows the decision date when the corpus recorded one', () => {
    render(<SourceList sources={[judgment]} language="en" />)
    expect(screen.getByText(/1963-04-05/)).toBeInTheDocument()
  })

  it('falls back to the kind, not to a placeholder, when nothing names it', () => {
    render(<SourceList sources={[card({ kind: 'judgment' })]} language="en" />)

    expect(screen.getByRole('link', { name: /court judgment/i })).toBeInTheDocument()
  })

  it('never renders the literal null or None where a field is missing', () => {
    const { container } = render(
      <SourceList sources={[card({ kind: 'judgment' })]} language="en" />,
    )
    expect(container.textContent).not.toMatch(/\b(null|None|undefined)\b/)
  })

  it('labels a statute and a judgment differently in words, not only in colour', () => {
    render(<SourceList sources={[statute, judgment]} language="en" />)
    expect(screen.getByText('Act or regulation')).toBeInTheDocument()
    expect(screen.getByText('Court judgment')).toBeInTheDocument()
  })

  it('keeps a titled statute linked to its government URL', () => {
    render(<SourceList sources={[statute]} language="en" />)
    expect(screen.getByRole('link', { name: /model tenancy act/i })).toHaveAttribute(
      'href',
      statute.source_url,
    )
  })

  it('shows the host so the reader knows where the link goes', () => {
    render(<SourceList sources={[statute]} language="en" />)
    expect(screen.getByText('Opens indiacode.nic.in')).toBeInTheDocument()
  })

  it('does not link a source whose URL is not https (M6)', () => {
    render(
      <SourceList
        sources={[card({ source_url: 'http://example.com/x', title: 'Bad Source' })]}
        language="en"
      />,
    )
    expect(screen.queryByRole('link', { name: /bad source/i })).not.toBeInTheDocument()
    expect(screen.getByText('Bad Source')).toBeInTheDocument()
  })

  it('still names a card whose new fields a server did not send', () => {
    // Deploy skew: an older API omits `kind` and the judgment fields
    // entirely. The card must degrade to the neutral kind word rather than
    // render a blank link or the word "undefined" -- `lib/api.ts` lets a
    // response through on `chunk_id`/`source_url` alone, on purpose, since a
    // blanked page is a worse failure than a vaguer label.
    const legacy = {
      chunk_id: 'legacy',
      source_url: 'https://www.indiacode.nic.in/handle/1',
    } as unknown as SourceCard

    const { container } = render(<SourceList sources={[legacy]} language="en" />)

    expect(screen.getByRole('link', { name: 'Government document' })).toBeInTheDocument()
    expect(container.textContent).not.toMatch(/\b(null|None|undefined)\b/)
  })

  it('renders nothing at all when there are no sources', () => {
    const { container } = render(<SourceList sources={[]} language="en" />)
    expect(container).toBeEmptyDOMElement()
  })

  it('localises the kind labels rather than hardcoding English', () => {
    render(<SourceList sources={[statute, card({ kind: 'judgment' })]} language="hi" />)
    expect(screen.getByText('अधिनियम या नियम')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'अदालत का फैसला' })).toBeInTheDocument()
  })
})
