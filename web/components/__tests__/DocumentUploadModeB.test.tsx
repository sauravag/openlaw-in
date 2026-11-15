import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import DocumentUpload from '../DocumentUpload'
import { getStrings } from '@/lib/strings'

vi.mock('@/lib/authMode', () => ({ AUTH_ENABLED: true }))

function setup(retention: 'none' | 'expires' | 'kept') {
  render(
    <DocumentUpload
      language="en"
      conversationId={null}
      retention={retention}
      isUploading={false}
      uploadingFilename={null}
      documentCount={0}
      onFileChosen={vi.fn()}
    />,
  )
}

describe('DocumentUpload in Mode B', () => {
  it('links the anonymous notice to sign-up', () => {
    setup('expires')
    expect(
      screen.getByRole('link', { name: getStrings('en').signInCta }),
    ).toHaveAttribute('href', '/signup')
  })

  it('offers no sign-in link to a signed-in reader', () => {
    setup('kept')
    expect(screen.queryByRole('link', { name: getStrings('en').signInCta })).not.toBeInTheDocument()
  })
})
