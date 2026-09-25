import { afterEach, describe, expect, it, vi } from 'vitest'
import { askQuestion, AskError } from '../api'
import type { AnswerData, RefusalData } from '../types'

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const baseAnswer: AnswerData = {
  answer: 'ok',
  sources: [],
  language: 'en',
  corpus_version: 'v2026.08',
  footer: 'footer',
  crisis_response: null,
  helplines: [{ number: '15100', label: 'Free legal aid (NALSA)' }],
  is_answerable: true,
  quota_remaining: 5,
}

describe('askQuestion', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('POSTs to the same-origin /ask proxy with credentials included', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { ...baseAnswer, language: 'hi' }))
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
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, baseAnswer))
    vi.stubGlobal('fetch', fetchMock)

    await askQuestion({ question: 'q', language: 'en' })

    const requestInit = fetchMock.mock.calls[0][1] as RequestInit
    const sentBody = JSON.parse(requestInit.body as string)
    expect(sentBody.language).toBe('en')
  })

  it('sets an AbortSignal so a stalled request does not hang forever', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, baseAnswer))
    vi.stubGlobal('fetch', fetchMock)

    await askQuestion({ question: 'q', language: 'en' })

    const requestInit = fetchMock.mock.calls[0][1] as RequestInit
    expect(requestInit.signal).toBeInstanceOf(AbortSignal)
  })

  it('returns a typed "answer" outcome on 200', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, baseAnswer)))

    const outcome = await askQuestion({ question: 'q', language: 'en' })

    expect(outcome).toEqual({ kind: 'answer', data: baseAnswer })
  })

  it('returns a typed "refused" outcome on 429, rather than throwing', async () => {
    const refusal: RefusalData = {
      detail: 'Free allowance used for this period.',
      quota_remaining: 0,
      signup_required: true,
      escalation_footer: 'Call 15100.',
      crisis_response: null,
      helplines: [{ number: '15100', label: 'Free legal aid (NALSA)' }],
      quota_reset_at: '2026-10-01',
      signup_url: 'https://openlaw.in/signup',
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(429, refusal)))

    const outcome = await askQuestion({ question: 'q', language: 'en' })

    expect(outcome).toEqual({ kind: 'refused', status: 429, data: refusal })
  })

  it('returns a typed "refused" outcome on a 500 carrying escalation_footer and crisis_response (C1)', async () => {
    // The exact shape app.py's `_unhandled_error_content` sends: no quota
    // fields at all, since an unhandled error has no quota decision.
    const serverErrorRefusal = {
      detail: 'Something went wrong on our end. Please try again.',
      escalation_footer: 'Call NALSA free legal aid: 15100.',
      crisis_response: 'Call 112 for help now.',
      helplines: [
        { number: '112', label: 'Emergency' },
        { number: '181', label: "Women's helpline" },
        { number: '1091', label: "Police women's helpline" },
        { number: '15100', label: 'Free legal aid (NALSA)' },
      ],
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(500, serverErrorRefusal)))

    const outcome = await askQuestion({ question: 'my husband beats me', language: 'en' })

    expect(outcome).toEqual({ kind: 'refused', status: 500, data: serverErrorRefusal })
  })

  it('falls back to the static helpline set, not a bare error, when the network fails outright (re-review HIGH)', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')))

    const outcome = await askQuestion({ question: 'q', language: 'en' })

    expect(outcome.kind).toBe('refused')
    if (outcome.kind === 'refused') {
      expect(outcome.data.crisis_response).toMatch(/could not reach the server/i)
      expect(outcome.data.helplines.length).toBeGreaterThan(0)
    }
  })

  it('falls back to the static Hindi helpline set when the request times out (re-review HIGH)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockRejectedValue(new DOMException('The operation was aborted.', 'TimeoutError')),
    )

    const outcome = await askQuestion({ question: 'q', language: 'hi' })

    expect(outcome.kind).toBe('refused')
    if (outcome.kind === 'refused') {
      expect(outcome.data.crisis_response).toMatch(/सर्वर से जुड़ नहीं सके/)
      expect(outcome.data.helplines).toContainEqual({ number: '112', label: 'आपातकालीन सेवा' })
    }
  })

  it('falls back to the static helpline set for a non-2xx body with no refusal shape (re-review HIGH)', async () => {
    // A load balancer's own JSON error page, or any 500 whose body isn't
    // shaped like `_unhandled_error_content`'s output.
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse(502, { error: 'Bad Gateway' })),
    )

    const outcome = await askQuestion({ question: 'q', language: 'en' })

    expect(outcome).toEqual({
      kind: 'refused',
      status: 502,
      data: expect.objectContaining({
        crisis_response: expect.stringMatching(/could not reach the server/i),
      }),
    })
  })

  it('falls back to the static helpline set for a non-JSON (e.g. proxy HTML error page) body (re-review HIGH)', async () => {
    const htmlResponse = new Response('<html><body>502 Bad Gateway</body></html>', {
      status: 502,
      headers: { 'Content-Type': 'text/html' },
    })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(htmlResponse))

    const outcome = await askQuestion({ question: 'q', language: 'en' })

    expect(outcome.kind).toBe('refused')
    if (outcome.kind === 'refused') {
      expect(outcome.data.helplines.length).toBeGreaterThan(0)
    }
  })

  it('throws AskError instead of returning a malformed 200 body (M2)', async () => {
    // Missing `sources` -- would otherwise throw a TypeError deep inside
    // AnswerView's render, with no error boundary to catch it.
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse(200, { answer: 'ok', language: 'en' })),
    )

    await expect(askQuestion({ question: 'q', language: 'en' })).rejects.toBeInstanceOf(
      AskError,
    )
  })
})
