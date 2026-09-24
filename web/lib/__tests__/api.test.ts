import { afterEach, describe, expect, it, vi } from 'vitest'
import { askQuestion, AskError } from '../api'
import type { AnswerData, RefusalData } from '../types'

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('askQuestion', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('POSTs to the same-origin /ask proxy with credentials included', async () => {
    const answer: AnswerData = {
      answer: 'ok',
      sources: [],
      language: 'hi',
      corpus_version: 'v2026.08',
      footer: 'footer',
      crisis_response: null,
      is_answerable: true,
      quota_remaining: 5,
    }
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, answer))
    vi.stubGlobal('fetch', fetchMock)

    await askQuestion({ question: 'What is bail?', language: 'hi' })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/openlaw/ask',
      expect.objectContaining({
        method: 'POST',
        credentials: 'include',
      }),
    )
    const requestInit = fetchMock.mock.calls[0][1] as RequestInit
    const sentBody = JSON.parse(requestInit.body as string)
    expect(sentBody.language).toBe('hi')
    expect(sentBody.question).toBe('What is bail?')
  })

  it('never leaves language unset -- it is always the caller-chosen value', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        answer: 'ok',
        sources: [],
        language: 'en',
        corpus_version: 'v2026.08',
        footer: 'footer',
        crisis_response: null,
        is_answerable: true,
        quota_remaining: 5,
      } satisfies AnswerData),
    )
    vi.stubGlobal('fetch', fetchMock)

    await askQuestion({ question: 'q', language: 'en' })

    const requestInit = fetchMock.mock.calls[0][1] as RequestInit
    const sentBody = JSON.parse(requestInit.body as string)
    expect(sentBody.language).toBe('en')
  })

  it('returns a typed "answer" outcome on 200', async () => {
    const answer: AnswerData = {
      answer: 'ok',
      sources: [],
      language: 'en',
      corpus_version: 'v2026.08',
      footer: 'footer',
      crisis_response: null,
      is_answerable: true,
      quota_remaining: 5,
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, answer)))

    const outcome = await askQuestion({ question: 'q', language: 'en' })

    expect(outcome).toEqual({ kind: 'answer', data: answer })
  })

  it('returns a typed "refused" outcome on 429, rather than throwing', async () => {
    const refusal: RefusalData = {
      detail: 'Free allowance used for this period.',
      quota_remaining: 0,
      signup_required: true,
      escalation_footer: 'Call 15100.',
      crisis_response: null,
      quota_reset_at: '2026-10-01',
      signup_url: 'https://openlaw.in/signup',
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(429, refusal)))

    const outcome = await askQuestion({ question: 'q', language: 'en' })

    expect(outcome).toEqual({ kind: 'refused', status: 429, data: refusal })
  })

  it('throws AskError when the network request fails outright', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')))

    await expect(askQuestion({ question: 'q', language: 'en' })).rejects.toBeInstanceOf(
      AskError,
    )
  })

  it('throws AskError for an unexpected non-429 error status', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse(500, { detail: 'server exploded' })),
    )

    await expect(askQuestion({ question: 'q', language: 'en' })).rejects.toThrow(
      'server exploded',
    )
  })
})
