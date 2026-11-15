import { render, screen } from '@testing-library/react'
import type { ReactElement } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { DELETE_CONFIRMATION_WORD, isDeleteConfirmed } from '@/lib/auth/deleteConfirmation'
import { getStrings } from '@/lib/strings'
import { expectNoEnglishStrings } from '@/test-utils/noEnglish'

const mode = vi.hoisted(() => ({ enabled: true }))
vi.mock('@/lib/authMode', () => ({
  get AUTH_ENABLED() {
    return mode.enabled
  },
}))

const readSession = vi.hoisted(() => vi.fn())
vi.mock('@/lib/auth/session', () => ({ readSession }))

import DeleteAccountPage from '../account/delete/page'

async function renderPage(params: Record<string, string> = {}) {
  const element = (await DeleteAccountPage({
    searchParams: Promise.resolve(params),
  })) as ReactElement
  return render(element)
}

beforeEach(() => {
  mode.enabled = true
  readSession.mockReset().mockResolvedValue({ email: 'person@example.com' })
})

describe('/account/delete', () => {
  it('posts a confirmation field and the language to the delete route', async () => {
    const { container } = await renderPage()

    const form = container.querySelector('form')
    expect(form).toHaveAttribute('method', 'post')
    expect(form).toHaveAttribute('action', '/api/auth/delete-account')
    expect(container.querySelector('input[name="confirmation"]')).toHaveAttribute('type', 'text')
    expect(container.querySelector('input[name="language"]')).toHaveAttribute('value', 'en')
  })

  it.each(['en', 'hi'] as const)('names the exact word to type in %s, and the route accepts it', async (language) => {
    await renderPage({ language })

    const word = DELETE_CONFIRMATION_WORD[language]
    expect(screen.getByText(getStrings(language).deleteAccountInstruction(word))).toBeInTheDocument()
    expect(isDeleteConfirmed(word, language)).toBe(true)
  })

  it('states what is erased, what is kept and what may remain, before the form', async () => {
    const { container } = await renderPage()
    const strings = getStrings('en')

    const html = container.innerHTML
    const formAt = html.indexOf('<form')
    for (const sentence of [
      strings.deleteAccountWillErase,
      strings.deleteAccountLogKept,
      strings.deleteAccountIdentityMayRemain,
    ]) {
      const at = html.indexOf(sentence)
      expect(at).toBeGreaterThan(-1)
      expect(at).toBeLessThan(formAt)
    }
  })

  it('includes the static helpline block', async () => {
    await renderPage({ language: 'hi' })

    expect(screen.getByRole('link', { name: /112/ })).toHaveAttribute('href', 'tel:112')
  })

  it('renders Hindi copy with no English dictionary sentence', async () => {
    const { container } = await renderPage({ language: 'hi' })

    expectNoEnglishStrings(container.textContent ?? '')
    expect(screen.getByRole('button')).toHaveTextContent(getStrings('hi').deleteAccountSubmit)
  })

  it.each([
    [{}, '/signup'],
    [{ language: 'hi' }, '/signup?language=hi'],
  ])('sends a signed-out visitor (%j) to %s', async (params, location) => {
    readSession.mockResolvedValue(null)

    await expect(
      DeleteAccountPage({ searchParams: Promise.resolve(params) }),
    ).rejects.toMatchObject({ digest: expect.stringContaining(`NEXT_REDIRECT;replace;${location};`) })
  })

  it.each([
    ['confirmation_required', 'accountDeleteConfirmationRequired'],
    ['failed', 'accountDeleteFailed'],
    ['too_large', 'accountDeleteTooLarge'],
    ['conflict', 'accountDeleteConflict'],
  ] as const)('renders the localised copy for ?error=%s, with helplines and the form', async (code, key) => {
    for (const language of ['en', 'hi'] as const) {
      const { container, unmount } = await renderPage({ error: code, language })

      expect(screen.getByRole('alert')).toHaveTextContent(getStrings(language)[key])
      expect(container.querySelector('form')).not.toBeNull()
      expect(screen.getByRole('link', { name: /112/ })).toBeInTheDocument()
      unmount()
    }
  })

  it.each(['<script>alert(1)</script>', 'constructor', '__proto__', 'nonsense'])(
    'renders the generic failure for unknown code %s and never echoes it',
    async (code) => {
      const { container } = await renderPage({ error: code })

      expect(screen.getByRole('alert')).toHaveTextContent(getStrings('en').accountDeleteFailed)
      expect(container.innerHTML).not.toContain(code)
    },
  )

  it('renders no alert without an error code', async () => {
    await renderPage()

    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('renders a Hindi error with no English string', async () => {
    const { container } = await renderPage({ error: 'conflict', language: 'hi' })

    expectNoEnglishStrings(container.textContent ?? '')
  })

  it('is a 404 in Mode A and never reads the session', async () => {
    mode.enabled = false

    await expect(DeleteAccountPage({ searchParams: Promise.resolve({}) })).rejects.toMatchObject({
      digest: 'NEXT_HTTP_ERROR_FALLBACK;404',
    })
    expect(readSession).not.toHaveBeenCalled()
  })
})
