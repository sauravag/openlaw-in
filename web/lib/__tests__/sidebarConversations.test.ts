import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchSidebarPage } from '../sidebarConversations'

const ITEM = {
  id: 'c1',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-02T00:00:00Z',
  document_count: 0,
}

function stubFetch(response: Partial<Response> | Error) {
  const fetchMock = vi.fn(async () => {
    if (response instanceof Error) throw response
    return response as Response
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function okJson(body: unknown): Partial<Response> {
  return { ok: true, status: 200, json: async () => body }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('fetchSidebarPage', () => {
  it('omits include_titles when titles are off', async () => {
    const fetchMock = stubFetch(okJson({ conversations: [], next_offset: null }))
    await fetchSidebarPage({ language: 'en', offset: 0, includeTitles: false })
    const url = String((fetchMock.mock.calls[0] as unknown[])[0])
    expect(url).toBe('/api/openlaw/conversations?language=en&offset=0')
    expect(url).not.toContain('include_titles')
  })

  it('sends include_titles=true when on', async () => {
    const fetchMock = stubFetch(okJson({ conversations: [], next_offset: null }))
    await fetchSidebarPage({ language: 'en', offset: 0, includeTitles: true })
    expect(String((fetchMock.mock.calls[0] as unknown[])[0])).toContain('&include_titles=true')
  })

  it('passes offset and sends credentials', async () => {
    const fetchMock = stubFetch(okJson({ conversations: [], next_offset: null }))
    await fetchSidebarPage({ language: 'hi', offset: 20, includeTitles: false })
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toContain('language=hi&offset=20')
    expect(init.credentials).toBe('include')
  })

  it('returns the page with titles and next offset', async () => {
    stubFetch(okJson({ conversations: [{ ...ITEM, title: 'Hello' }], next_offset: 20 }))
    const outcome = await fetchSidebarPage({ language: 'en', offset: 0, includeTitles: true })
    expect(outcome).toEqual({
      kind: 'page',
      conversations: [{ ...ITEM, title: 'Hello' }],
      nextOffset: 20,
    })
  })

  it('returns failed on network error', async () => {
    stubFetch(new Error('offline'))
    expect(await fetchSidebarPage({ language: 'en', offset: 0, includeTitles: false })).toEqual({
      kind: 'failed',
    })
  })

  it('returns failed on a non-2xx response', async () => {
    stubFetch({ ok: false, status: 401, json: async () => ({}) })
    expect(await fetchSidebarPage({ language: 'en', offset: 0, includeTitles: false })).toEqual({
      kind: 'failed',
    })
  })

  it('returns failed on a malformed body', async () => {
    stubFetch(okJson({ conversations: 'nope', next_offset: null }))
    expect(await fetchSidebarPage({ language: 'en', offset: 0, includeTitles: false })).toEqual({
      kind: 'failed',
    })
  })

  it('returns failed when a title is neither string nor null', async () => {
    stubFetch(okJson({ conversations: [{ ...ITEM, title: 5 }], next_offset: null }))
    expect(await fetchSidebarPage({ language: 'en', offset: 0, includeTitles: true })).toEqual({
      kind: 'failed',
    })
  })

  it('returns failed when the body is not JSON', async () => {
    stubFetch({
      ok: true,
      status: 200,
      json: async () => {
        throw new SyntaxError('bad')
      },
    })
    expect(await fetchSidebarPage({ language: 'en', offset: 0, includeTitles: false })).toEqual({
      kind: 'failed',
    })
  })
})
