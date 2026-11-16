import { afterEach, describe, expect, it, vi } from 'vitest'
import { readAuthAgreement, retryMigration } from '../api'

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const refusal = {
  detail: 'Sign in to see your conversations.',
  escalation_footer: 'Call NALSA free legal aid: 15100.',
  crisis_response: null,
  helplines: [{ number: '15100', label: 'Free legal aid (NALSA)' }],
}

describe('retryMigration (final review H2)', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('POSTs the language to the same-origin migrate route', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, { status: 'migrated', conversations_moved: 1, documents_moved: 0 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    expect(await retryMigration({ language: 'hi' })).toEqual({ kind: 'migrated' })
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/auth/migrate')
    expect(init).toMatchObject({ method: 'POST', credentials: 'include' })
    expect(JSON.parse(init.body)).toEqual({ language: 'hi' })
  })

  it('resolves a refusal, with help, never throwing', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(502, refusal)))
    expect(await retryMigration({ language: 'en' })).toEqual({ kind: 'refused', status: 502, data: refusal })

    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')))
    expect(await retryMigration({ language: 'en' })).toMatchObject({ kind: 'refused', status: 0 })
  })
})

describe('readAuthAgreement (final review H3c)', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('reads agrees from the same-origin status route, uncached', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { agrees: false }))
    vi.stubGlobal('fetch', fetchMock)

    expect(await readAuthAgreement()).toBe(false)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/openlaw/auth-status',
      expect.objectContaining({ cache: 'no-store' }),
    )
  })

  it.each([
    ['a true answer', () => Promise.resolve(jsonResponse(200, { agrees: true })), true],
    ['an unreachable backend', () => Promise.resolve(jsonResponse(200, { agrees: null })), null],
    ['a malformed body', () => Promise.resolve(jsonResponse(200, { agrees: 'no' })), null],
    ['a server error', () => Promise.resolve(jsonResponse(500, {})), null],
    ['a network failure', () => Promise.reject(new TypeError('offline')), null],
  ])('maps %s', async (_label, respond, expected) => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation(respond))
    expect(await readAuthAgreement()).toBe(expected)
  })
})
