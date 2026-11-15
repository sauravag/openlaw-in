import { afterEach, describe, expect, it, vi } from 'vitest'
import { CONVERSATIONS_ENDPOINT, listConversations, retryMigration } from '../api'

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const list = {
  conversations: [
    {
      id: 'conv-1',
      created_at: '2026-11-14T10:00:00+00:00',
      updated_at: '2026-11-14T11:00:00+00:00',
      document_count: 2,
    },
  ],
  next_offset: null,
}

const refusal = {
  detail: 'Sign in to see your conversations.',
  escalation_footer: 'Call NALSA free legal aid: 15100.',
  crisis_response: null,
  helplines: [{ number: '15100', label: 'Free legal aid (NALSA)' }],
}

describe('listConversations (final review H1b)', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('GETs the same-origin list with credentials and the language', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, list))
    vi.stubGlobal('fetch', fetchMock)

    expect(await listConversations({ language: 'hi' })).toEqual({ kind: 'list', data: list })
    expect(fetchMock).toHaveBeenCalledWith(
      `${CONVERSATIONS_ENDPOINT}?language=hi`,
      expect.objectContaining({ method: 'GET', credentials: 'include' }),
    )
  })

  it('resolves a 401 to a help-carrying refusal, never throwing', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(401, refusal)))

    expect(await listConversations({ language: 'en' })).toEqual({
      kind: 'refused',
      status: 401,
      data: refusal,
    })
  })

  it('treats a malformed list as a refusal with the fallback helplines', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { conversations: [{}] })))

    const outcome = await listConversations({ language: 'en' })

    expect(outcome.kind).toBe('refused')
    if (outcome.kind === 'refused') expect(outcome.data.helplines.length).toBeGreaterThan(0)
  })

  it('treats a network failure as a refusal with the fallback helplines', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')))

    const outcome = await listConversations({ language: 'en' })

    expect(outcome).toMatchObject({ kind: 'refused', status: 0 })
  })
})

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
