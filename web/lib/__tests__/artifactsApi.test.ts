import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  CONVERSATIONS_ENDPOINT,
  DOCUMENTS_ENDPOINT,
  deleteConversation,
  deleteDocument,
  readConversation,
  uploadDocument,
} from '../api'
import { MAX_DOCUMENTS_PER_CONVERSATION, MAX_UPLOAD_BYTES } from '../documents'
import type { DocumentSummaryData } from '../types'

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const summary: DocumentSummaryData = {
  id: 'doc-1',
  filename: 'lease.pdf',
  page_count: 4,
  conversation_id: 'conv-1',
  expires_at: null,
  retention_notice: 'Stored encrypted; stays until you delete it.',
  quota_remaining: 9,
}

const refusalBody = {
  detail: 'That file is too large.',
  escalation_footer: 'Call NALSA free legal aid: 15100.',
  crisis_response: null,
  helplines: [{ number: '15100', label: 'Free legal aid (NALSA)' }],
}

function pdf(bytes = 10): File {
  return new File(['x'.repeat(bytes)], 'lease.pdf', { type: 'application/pdf' })
}

describe('uploadDocument', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('POSTs multipart form data to the same-origin proxy with credentials included', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(201, summary))
    vi.stubGlobal('fetch', fetchMock)

    const outcome = await uploadDocument({
      file: pdf(),
      conversationId: 'conv-1',
      language: 'hi',
      heldDocumentCount: 0,
    })

    expect(outcome).toEqual({ kind: 'document', data: summary })
    expect(fetchMock).toHaveBeenCalledWith(
      DOCUMENTS_ENDPOINT,
      expect.objectContaining({ method: 'POST', credentials: 'include' }),
    )
    const body = fetchMock.mock.calls[0][1].body as FormData
    expect(body.get('language')).toBe('hi')
    expect(body.get('conversation_id')).toBe('conv-1')
    expect(body.get('file')).toBeInstanceOf(File)
  })

  it('omits conversation_id on a first upload so the server mints the conversation', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(201, summary))
    vi.stubGlobal('fetch', fetchMock)

    await uploadDocument({
      file: pdf(),
      conversationId: null,
      language: 'en',
      heldDocumentCount: 0,
    })

    const body = fetchMock.mock.calls[0][1].body as FormData
    expect(body.has('conversation_id')).toBe(false)
  })

  it('never lets the browser set its own Content-Type, which would break the boundary', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(201, summary))
    vi.stubGlobal('fetch', fetchMock)

    await uploadDocument({
      file: pdf(),
      conversationId: null,
      language: 'en',
      heldDocumentCount: 0,
    })

    expect(fetchMock.mock.calls[0][1].headers).toBeUndefined()
  })

  it('refuses an oversized file before a single byte leaves the phone', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    const outcome = await uploadDocument({
      file: pdf(MAX_UPLOAD_BYTES + 1),
      conversationId: null,
      language: 'en',
      heldDocumentCount: 0,
    })

    expect(fetchMock).not.toHaveBeenCalled()
    expect(outcome.kind).toBe('refused')
    if (outcome.kind === 'refused') {
      expect(outcome.data.helplines.length).toBeGreaterThan(0)
    }
  })

  it('refuses one document past the per-conversation cap without a round trip', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    const outcome = await uploadDocument({
      file: pdf(),
      conversationId: 'conv-1',
      language: 'en',
      heldDocumentCount: MAX_DOCUMENTS_PER_CONVERSATION,
    })

    expect(fetchMock).not.toHaveBeenCalled()
    expect(outcome.kind).toBe('refused')
  })

  it('passes a server refusal through with its helplines intact', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(413, refusalBody)))

    const outcome = await uploadDocument({
      file: pdf(),
      conversationId: null,
      language: 'en',
      heldDocumentCount: 0,
    })

    expect(outcome).toEqual({ kind: 'refused', status: 413, data: refusalBody })
  })

  it('falls back to the static helpline set when the network fails outright', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')))

    const outcome = await uploadDocument({
      file: pdf(),
      conversationId: null,
      language: 'en',
      heldDocumentCount: 0,
    })

    expect(outcome.kind).toBe('refused')
    if (outcome.kind === 'refused') {
      expect(outcome.data.helplines.map((helpline) => helpline.number)).toContain('112')
    }
  })

  it('treats a 201 with an unrecognisable body as a refusal, never as a stored document', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(201, { nope: true })))

    const outcome = await uploadDocument({
      file: pdf(),
      conversationId: null,
      language: 'en',
      heldDocumentCount: 0,
    })

    expect(outcome.kind).toBe('refused')
  })
})

describe('deleteDocument', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('DELETEs the document and reports success on the 204', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetchMock)

    const outcome = await deleteDocument({ documentId: 'doc-1', language: 'en' })

    expect(outcome).toEqual({ kind: 'deleted' })
    expect(fetchMock).toHaveBeenCalledWith(
      `${DOCUMENTS_ENDPOINT}/doc-1`,
      expect.objectContaining({ method: 'DELETE', credentials: 'include' }),
    )
  })

  it('resolves to a help-carrying refusal when the server says no', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse(404, { ...refusalBody, detail: 'Not found.' })),
    )

    const outcome = await deleteDocument({ documentId: 'doc-1', language: 'en' })

    expect(outcome.kind).toBe('refused')
    if (outcome.kind === 'refused') {
      expect(outcome.data.detail).toBe('Not found.')
      expect(outcome.data.helplines.length).toBeGreaterThan(0)
    }
  })

  it('never throws, even when the network is gone', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')))

    const outcome = await deleteDocument({ documentId: 'doc-1', language: 'en' })

    expect(outcome.kind).toBe('refused')
  })

  it('refuses a document id that is not a UUID-shaped path segment', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    const outcome = await deleteDocument({ documentId: '../conversations/c', language: 'en' })

    expect(fetchMock).not.toHaveBeenCalled()
    expect(outcome.kind).toBe('refused')
  })
})

describe('deleteConversation', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('DELETEs the conversation and reports success on the 204', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetchMock)

    const outcome = await deleteConversation({ conversationId: 'conv-1', language: 'en' })

    expect(outcome).toEqual({ kind: 'deleted' })
    expect(fetchMock).toHaveBeenCalledWith(
      `${CONVERSATIONS_ENDPOINT}/conv-1`,
      expect.objectContaining({ method: 'DELETE' }),
    )
  })

  it('never throws on a failure', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')))

    expect((await deleteConversation({ conversationId: 'c', language: 'en' })).kind).toBe(
      'refused',
    )
  })
})

describe('readConversation', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('returns the stored turns for rendering after a reload', async () => {
    const view = {
      conversation_id: 'conv-1',
      turns: [
        {
          turn_index: 0,
          question: 'q',
          answer: 'a',
          document_answer: null,
          language: 'en',
        },
      ],
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, view)))

    expect(await readConversation({ conversationId: 'conv-1', language: 'en' })).toEqual({
      kind: 'conversation',
      data: view,
    })
  })

  it('resolves to a refusal rather than throwing when the conversation is gone', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(404, refusalBody)))

    expect((await readConversation({ conversationId: 'c', language: 'en' })).kind).toBe(
      'refused',
    )
  })

  it('treats a 200 with a body that is not a conversation as a refusal', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { turns: 'nope' })))

    expect((await readConversation({ conversationId: 'c', language: 'en' })).kind).toBe(
      'refused',
    )
  })
})
