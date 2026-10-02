import { describe, expect, it } from 'vitest'
import {
  ALLOWED_UPLOAD_TYPES,
  ANONYMOUS_UPLOAD_TTL_HOURS,
  MAX_DOCUMENTS_PER_CONVERSATION,
  MAX_UPLOAD_BYTES,
  attachedFromConversationCard,
  attachedFromSummary,
  localUploadProblem,
  uploadRefusal,
  withDocument,
  withoutDocument,
  type AttachedDocument,
} from '@/lib/documents'
import type { DocumentSummaryData } from '@/lib/types'

function summary(overrides: Partial<DocumentSummaryData> = {}): DocumentSummaryData {
  return {
    id: 'doc-1',
    filename: 'lease.pdf',
    page_count: 4,
    conversation_id: 'conv-1',
    expires_at: null,
    retention_notice: 'Stored encrypted; stays until you delete it.',
    quota_remaining: 7,
    ...overrides,
  }
}

function attached(overrides: Partial<AttachedDocument> = {}): AttachedDocument {
  return { ...attachedFromSummary(summary()), ...overrides }
}

/** jsdom has `File`; size is derived from the parts, so a padded string is
 * the cheapest way to make a file of a known length. */
function file(name: string, type: string, bytes: number): File {
  return new File(['x'.repeat(bytes)], name, { type })
}

describe('attachedFromSummary', () => {
  it('keeps the server-composed retention notice verbatim', () => {
    const document = attachedFromSummary(summary())
    expect(document.retentionNotice).toBe('Stored encrypted; stays until you delete it.')
    expect(document.id).toBe('doc-1')
    expect(document.filename).toBe('lease.pdf')
    expect(document.pageCount).toBe(4)
  })
})

describe('withDocument / withoutDocument', () => {
  it('returns a new array and never mutates the one passed in', () => {
    const existing: AttachedDocument[] = [attached()]
    const added = withDocument(existing, attached({ id: 'doc-2', filename: 'fir.pdf' }))

    expect(added).toHaveLength(2)
    expect(existing).toHaveLength(1)
    expect(added).not.toBe(existing)
  })

  it('removes only the named document, without mutating the original list', () => {
    const existing: AttachedDocument[] = [attached(), attached({ id: 'doc-2' })]
    const remaining = withoutDocument(existing, 'doc-1')

    expect(remaining.map((item) => item.id)).toEqual(['doc-2'])
    expect(existing.map((item) => item.id)).toEqual(['doc-1', 'doc-2'])
  })
})

describe('localUploadProblem', () => {
  it('accepts every content type the server can extract text from', () => {
    for (const type of ALLOWED_UPLOAD_TYPES) {
      expect(localUploadProblem(file('f', type, 10), 0)).toBeNull()
    }
  })

  it('refuses a file over the size cap before any byte is uploaded', () => {
    expect(localUploadProblem(file('big.pdf', 'application/pdf', MAX_UPLOAD_BYTES + 1), 0)).toBe(
      'too_large',
    )
  })

  it('refuses a content type with no text extractor (an image, for instance)', () => {
    expect(localUploadProblem(file('scan.jpg', 'image/jpeg', 10), 0)).toBe('unsupported_type')
  })

  it('refuses once the conversation already holds its maximum documents', () => {
    expect(
      localUploadProblem(
        file('f.pdf', 'application/pdf', 10),
        MAX_DOCUMENTS_PER_CONVERSATION,
      ),
    ).toBe('too_many_documents')
  })

  it('refuses when nothing was chosen at all', () => {
    expect(localUploadProblem(null, 0)).toBe('no_file')
  })
})

describe('uploadRefusal', () => {
  it('carries helplines for every locally detected failure, in the reader language', () => {
    for (const problem of ['too_large', 'unsupported_type', 'too_many_documents', 'no_file'] as const) {
      const refusal = uploadRefusal(problem, 'hi')
      expect(refusal.detail.length).toBeGreaterThan(0)
      expect(refusal.escalation_footer.length).toBeGreaterThan(0)
      expect(refusal.helplines.map((helpline) => helpline.number)).toContain('15100')
      expect(refusal.helplines.map((helpline) => helpline.number)).toContain('112')
    }
  })

  it('does not invent a crisis the person never described', () => {
    // A file that was too big is not an emergency. The numbers are there to
    // be found; claiming the reader is in danger would be a lie.
    expect(uploadRefusal('too_large', 'en').crisis_response).toBeNull()
  })

  it('names the actual limit rather than a vague "too big"', () => {
    expect(uploadRefusal('too_large', 'en').detail).toMatch(/10/)
    expect(uploadRefusal('too_many_documents', 'en').detail).toMatch(
      String(MAX_DOCUMENTS_PER_CONVERSATION),
    )
  })
})

describe('mirrored server limits', () => {
  it('matches openlaw.config.Settings defaults', () => {
    // Checked by eye against `src/openlaw/config.py`; the server is the
    // enforcer, these exist only so the copy can state the limit honestly
    // and an obvious failure costs no upload.
    expect(MAX_UPLOAD_BYTES).toBe(10_485_760)
    expect(MAX_DOCUMENTS_PER_CONVERSATION).toBe(3)
    expect(ANONYMOUS_UPLOAD_TTL_HOURS).toBe(24)
    expect([...ALLOWED_UPLOAD_TYPES]).toEqual([
      'application/pdf',
      'text/plain',
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    ])
  })
})

describe('attachedFromConversationCard', () => {
  it('produces the same shape an upload does, so a restored file is identical', () => {
    const restored = attachedFromConversationCard({
      id: 'doc-1',
      filename: 'lease.pdf',
      page_count: 3,
      expires_at: null,
      retention_notice: 'Stays until you delete it.',
    })

    expect(restored).toEqual({
      id: 'doc-1',
      filename: 'lease.pdf',
      pageCount: 3,
      retentionNotice: 'Stays until you delete it.',
      expiresAt: null,
    })
  })

  it('carries no field that could hold document content', () => {
    const restored = attachedFromConversationCard({
      id: 'doc-1',
      filename: 'lease.pdf',
      page_count: 1,
      expires_at: '2026-10-02T00:00:00+00:00',
      retention_notice: 'Deleted in 24 hours.',
    })

    expect(Object.keys(restored).sort()).toEqual([
      'expiresAt',
      'filename',
      'id',
      'pageCount',
      'retentionNotice',
    ])
  })
})
