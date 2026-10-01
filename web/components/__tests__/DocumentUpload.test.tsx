import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import DocumentUpload from '../DocumentUpload'
import { DOCUMENTS_ENDPOINT } from '@/lib/api'
import {
  ANONYMOUS_UPLOAD_TTL_HOURS,
  MAX_DOCUMENTS_PER_CONVERSATION,
  MAX_UPLOAD_MEGABYTES,
} from '@/lib/documents'
import { getStrings } from '@/lib/strings'

function setup(props: Partial<React.ComponentProps<typeof DocumentUpload>> = {}) {
  const onFileChosen = vi.fn()
  render(
    <DocumentUpload
      language="en"
      conversationId={null}
      isSignedIn={false}
      isUploading={false}
      uploadingFilename={null}
      documentCount={0}
      onFileChosen={onFileChosen}
      {...props}
    />,
  )
  return { onFileChosen }
}

describe('DocumentUpload', () => {
  it('posts multipart form data to the real endpoint, so nothing is lost with JavaScript off', () => {
    setup()
    const form = screen.getByLabelText(getStrings('en').attachFileLabel).closest('form')
    expect(form).toHaveAttribute('method', 'post')
    expect(form).toHaveAttribute('enctype', 'multipart/form-data')
    expect(form).toHaveAttribute('action', DOCUMENTS_ENDPOINT)
    // The field name the FastAPI handler reads (`file: UploadFile = File(...)`).
    expect(screen.getByLabelText(getStrings('en').attachFileLabel)).toHaveAttribute('name', 'file')
  })

  it('carries the language and conversation as form fields for the no-JavaScript post', () => {
    setup({ conversationId: 'conv-1', language: 'hi' })
    const form = screen.getByLabelText(getStrings('hi').attachFileLabel).closest('form')
    expect(form?.querySelector('input[name="language"]')).toHaveValue('hi')
    expect(form?.querySelector('input[name="conversation_id"]')).toHaveValue('conv-1')
  })

  it('states the limits before a file is chosen', () => {
    setup()
    const strings = getStrings('en')
    expect(
      screen.getByText(
        strings.uploadLimitsNote(MAX_UPLOAD_MEGABYTES, MAX_DOCUMENTS_PER_CONVERSATION),
      ),
    ).toBeInTheDocument()
  })

  it('tells an anonymous visitor their document expires, and offers the one fix', () => {
    setup({ isSignedIn: false })
    const strings = getStrings('en')
    expect(
      screen.getByText(strings.anonymousUploadNotice(ANONYMOUS_UPLOAD_TTL_HOURS)),
    ).toBeInTheDocument()
    expect(screen.getByRole('link', { name: strings.signInCta })).toHaveAttribute('href', '/signup')
  })

  it('does not warn a signed-in reader about an expiry that will not happen', () => {
    setup({ isSignedIn: true })
    expect(
      screen.queryByText(getStrings('en').anonymousUploadNotice(ANONYMOUS_UPLOAD_TTL_HOURS)),
    ).not.toBeInTheDocument()
  })

  it('hands the chosen file straight to the page, without waiting for a submit', async () => {
    const { onFileChosen } = setup()
    const file = new File(['hello'], 'lease.pdf', { type: 'application/pdf' })

    await userEvent.upload(screen.getByLabelText(getStrings('en').attachFileLabel), file)

    expect(onFileChosen).toHaveBeenCalledWith(file)
  })

  it('reports progress while a file is in flight, and blocks a second pick', () => {
    setup({ isUploading: true, uploadingFilename: 'lease.pdf' })
    expect(screen.getByRole('status')).toHaveTextContent('lease.pdf')
    expect(screen.getByLabelText(getStrings('en').attachFileLabel)).toBeDisabled()
  })

  it('stops offering an attachment once the conversation is full', () => {
    setup({ documentCount: MAX_DOCUMENTS_PER_CONVERSATION })
    expect(screen.queryByLabelText(getStrings('en').attachFileLabel)).not.toBeInTheDocument()
    expect(
      screen.getByText(
        getStrings('en').uploadFullNote(MAX_DOCUMENTS_PER_CONVERSATION),
      ),
    ).toBeInTheDocument()
  })

  it('renders every one of its own strings in Hindi', () => {
    setup({ language: 'hi' })
    const strings = getStrings('hi')
    expect(screen.getByText(strings.documentsIntro)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: strings.attachButton })).toBeInTheDocument()
  })
})
