import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import DocumentCitationList from '../DocumentCitationList'
import { getStrings } from '@/lib/strings'
import type { DocumentCitationCard } from '@/lib/types'

const citation: DocumentCitationCard = {
  excerpt_id: 'doc:1',
  document_id: 'd1',
  filename: 'lease.pdf',
  page_number: 4,
}

const documentAnswer =
  'From the document you uploaded (this is what your paper says, not the law):\n\nClause 7 requires one month of notice.'

describe('DocumentCitationList', () => {
  it('renders nothing at all when the turn produced no document content', () => {
    const { container } = render(
      <DocumentCitationList documentAnswer={null} citations={[]} language="en" />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('never renders a document citation as a link', () => {
    // `DocumentCitationCard` has no `source_url` by construction, so there is
    // nothing to link to -- and a link here would be the exact confusion this
    // whole section exists to prevent: a reader clicking what looks like a
    // government source and landing on their own paperwork.
    const { container } = render(
      <DocumentCitationList
        documentAnswer={documentAnswer}
        citations={[citation, { ...citation, excerpt_id: 'doc:2', page_number: 9 }]}
        language="en"
      />,
    )
    expect(container.querySelectorAll('a')).toHaveLength(0)
  })

  it('heads the section "From your document" and says plainly it is not law', () => {
    render(
      <DocumentCitationList
        documentAnswer={documentAnswer}
        citations={[citation]}
        language="en"
      />,
    )
    const strings = getStrings('en')
    const region = screen.getByRole('region', { name: strings.documentCitationsHeading })
    expect(region).toHaveTextContent(strings.documentCitationsHeading)
    expect(region).toHaveTextContent(strings.documentNotLawNote)
  })

  it('identifies each excerpt by the filename and page a reader can check', () => {
    render(
      <DocumentCitationList
        documentAnswer={documentAnswer}
        citations={[citation]}
        language="en"
      />,
    )
    expect(screen.getByText('lease.pdf')).toBeInTheDocument()
    expect(screen.getByText(getStrings('en').documentPageLabel(4))).toBeInTheDocument()
  })

  it('renders the document prose the server composed, without adding to it', () => {
    render(
      <DocumentCitationList
        documentAnswer={documentAnswer}
        citations={[citation]}
        language="en"
      />,
    )
    expect(screen.getByTestId('document-answer')).toHaveTextContent(
      'Clause 7 requires one month of notice.',
    )
  })

  it('renders every one of its own strings in Hindi when the turn is Hindi', () => {
    render(
      <DocumentCitationList
        documentAnswer={documentAnswer}
        citations={[citation]}
        language="hi"
      />,
    )
    const strings = getStrings('hi')
    expect(
      screen.getByRole('region', { name: strings.documentCitationsHeading }),
    ).toBeInTheDocument()
    expect(screen.getByText(strings.documentNotLawNote)).toBeInTheDocument()
    expect(screen.getByText(strings.documentPageLabel(4))).toBeInTheDocument()
  })

  it('shows the citations even when no document prose survived validation', () => {
    render(
      <DocumentCitationList documentAnswer={null} citations={[citation]} language="en" />,
    )
    expect(screen.getByText('lease.pdf')).toBeInTheDocument()
    expect(screen.queryByTestId('document-answer')).not.toBeInTheDocument()
  })
})
