import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import DocumentList from '../DocumentList'
import { getStrings } from '@/lib/strings'
import type { AttachedDocument } from '@/lib/documents'

const lease: AttachedDocument = {
  id: 'd1',
  filename: 'lease.pdf',
  pageCount: 4,
  retentionNotice: 'This file is stored encrypted and stays until you delete it.',
  expiresAt: null,
}

describe('DocumentList', () => {
  it('renders nothing when no document is attached', () => {
    const { container } = render(
      <DocumentList documents={[]} language="en" onDelete={vi.fn()} deletingId={null} />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('names the file being deleted in the control an assistive reader announces', () => {
    render(
      <DocumentList documents={[lease]} language="en" onDelete={vi.fn()} deletingId={null} />,
    )
    expect(
      screen.getByRole('button', { name: getStrings('en').deleteDocumentLabel('lease.pdf') }),
    ).toBeInTheDocument()
  })

  it('calls back with the document id when the delete control is used', async () => {
    const onDelete = vi.fn()
    render(
      <DocumentList documents={[lease]} language="en" onDelete={onDelete} deletingId={null} />,
    )

    await userEvent.click(
      screen.getByRole('button', { name: getStrings('en').deleteDocumentLabel('lease.pdf') }),
    )

    expect(onDelete).toHaveBeenCalledWith('d1')
  })

  it('shows the server retention notice verbatim, so the promise made is the promise kept', () => {
    render(
      <DocumentList documents={[lease]} language="en" onDelete={vi.fn()} deletingId={null} />,
    )
    expect(screen.getByText(lease.retentionNotice)).toBeInTheDocument()
  })

  it('disables the delete control only for the document currently being deleted', () => {
    const fir: AttachedDocument = { ...lease, id: 'd2', filename: 'fir.pdf' }
    const strings = getStrings('en')
    render(
      <DocumentList documents={[lease, fir]} language="en" onDelete={vi.fn()} deletingId="d1" />,
    )
    expect(
      screen.getByRole('button', { name: strings.deleteDocumentLabel('lease.pdf') }),
    ).toBeDisabled()
    expect(
      screen.getByRole('button', { name: strings.deleteDocumentLabel('fir.pdf') }),
    ).toBeEnabled()
  })

  it('says which question these documents will be read for, in Hindi too', () => {
    render(
      <DocumentList documents={[lease]} language="hi" onDelete={vi.fn()} deletingId={null} />,
    )
    const strings = getStrings('hi')
    expect(screen.getByText(strings.attachedToNextTurnLabel)).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: strings.deleteDocumentLabel('lease.pdf') }),
    ).toBeInTheDocument()
  })
})
