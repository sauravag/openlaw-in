import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import SourceList from '../SourceList'
import type { SourceCard } from '@/lib/types'

const statute: SourceCard = {
  chunk_id: 'statute-1',
  source_url: 'https://www.indiacode.nic.in/handle/123456789/2385',
  title: 'Model Tenancy Act',
}

// The real shape of a Supreme Court citation today: `openlaw.api.schemas
// .SourceCard` carries only chunk_id/source_url/title, and the judgment
// payload has no `title` key, so `title` arrives as null and the URL is a
// tarball nobody can read.
const judgment: SourceCard = {
  chunk_id: 'judgment-1',
  source_url:
    'https://indian-supreme-court-judgments.s3.amazonaws.com/data/tar/1963_INSC_244.tar',
  title: null,
}

describe('SourceList', () => {
  it('renders an untitled judgment with an honest label, not a raw tarball URL', () => {
    render(<SourceList sources={[judgment]} language="en" />)

    const link = screen.getByRole('link', { name: /court judgment/i })
    expect(link).toHaveAttribute('href', judgment.source_url)
    // The old UI printed the whole S3 path as the link text.
    expect(screen.queryByText(/s3\.amazonaws\.com\/data\/tar/)).not.toBeInTheDocument()
  })

  it('never renders the literal null or None where a title is missing', () => {
    const { container } = render(<SourceList sources={[judgment]} language="en" />)
    expect(container.textContent).not.toMatch(/\b(null|None|undefined)\b/)
  })

  it('labels a statute and a judgment differently in words, not only in colour', () => {
    render(<SourceList sources={[statute, judgment]} language="en" />)
    expect(screen.getByText('Act or regulation')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /court judgment/i })).toBeInTheDocument()
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
        sources={[{ chunk_id: 'x', source_url: 'http://example.com/x', title: 'Bad Source' }]}
        language="en"
      />,
    )
    expect(screen.queryByRole('link', { name: /bad source/i })).not.toBeInTheDocument()
    expect(screen.getByText('Bad Source')).toBeInTheDocument()
  })

  it('renders nothing at all when there are no sources', () => {
    const { container } = render(<SourceList sources={[]} language="en" />)
    expect(container).toBeEmptyDOMElement()
  })

  it('localises the kind labels rather than hardcoding English', () => {
    render(<SourceList sources={[statute, judgment]} language="hi" />)
    expect(screen.getByText('अधिनियम या नियम')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'अदालत का फैसला' })).toBeInTheDocument()
  })
})
