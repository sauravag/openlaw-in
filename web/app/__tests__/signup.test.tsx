import { render, screen } from '@testing-library/react'
import type { ReactElement } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getFallbackHelplineSet } from '@/lib/fallbackHelplines'
import { getStrings } from '@/lib/strings'
import { expectNoEnglishStrings } from '@/test-utils/noEnglish'

const mode = vi.hoisted(() => ({ enabled: true }))
vi.mock('@/lib/authMode', () => ({
  get AUTH_ENABLED() {
    return mode.enabled
  },
}))

import SignupPage from '../signup/page'

async function renderPage(params: Record<string, string | string[]> = {}) {
  const element = (await SignupPage({ searchParams: Promise.resolve(params) })) as ReactElement
  return render(element)
}

beforeEach(() => {
  mode.enabled = true
})

describe('/signup', () => {
  it('renders the form and the helpline block by default', async () => {
    const { container } = await renderPage()

    expect(container.querySelector('form')).toHaveAttribute('action', '/api/auth/sign-in')
    expect(screen.getByRole('link', { name: /112/ })).toHaveAttribute('href', 'tel:112')
  })

  it('renders the check-your-email copy for ?sent=1, with helplines and no form', async () => {
    const { container } = await renderPage({ sent: '1' })

    expect(screen.getByText(getStrings('en').authLinkSent)).toBeInTheDocument()
    expect(container.querySelector('form')).toBeNull()
    expect(screen.getByRole('link', { name: /112/ })).toBeInTheDocument()
  })

  it.each([
    ['expired', 'authLinkExpired'],
    ['failed', 'authFailed'],
    ['rate_limited', 'authRateLimited'],
    ['invalid_email', 'authInvalidEmail'],
    ['too_large', 'authRequestTooLarge'],
    ['unavailable', 'authUnavailable'],
  ] as const)('renders the localised copy for ?error=%s with helplines and a retry form', async (code, key) => {
    for (const language of ['en', 'hi'] as const) {
      const { container, unmount } = await renderPage({ error: code, language })

      expect(screen.getByRole('alert')).toHaveTextContent(getStrings(language)[key])
      expect(container.querySelector('form')).not.toBeNull()
      const helpline = getFallbackHelplineSet(language).helplines[0]
      expect(screen.getByRole('link', { name: new RegExp(helpline.number) })).toBeInTheDocument()
      unmount()
    }
  })

  it.each(['<script>alert(1)</script>', 'constructor', '__proto__', 'totally_unknown'])(
    'renders the generic failed copy for unknown code %s and never echoes it',
    async (code) => {
      const { container } = await renderPage({ error: code })

      expect(screen.getByRole('alert')).toHaveTextContent(getStrings('en').authFailed)
      expect(container.innerHTML).not.toContain(code)
    },
  )

  it('uses the first value when a parameter is repeated', async () => {
    await renderPage({ error: ['rate_limited', 'failed'] })

    expect(screen.getByRole('alert')).toHaveTextContent(getStrings('en').authRateLimited)
  })

  it('lets an error win over sent', async () => {
    await renderPage({ error: 'failed', sent: '1' })

    expect(screen.queryByText(getStrings('en').authLinkSent)).toBeNull()
  })

  it.each(['sent', 'error'] as const)('renders the Hindi %s state with no English string', async (state) => {
    const params: Record<string, string> = state === 'sent' ? { sent: '1', language: 'hi' } : { error: 'rate_limited', language: 'hi' }
    const { container } = await renderPage(params)

    expectNoEnglishStrings(container.textContent ?? '')
  })

  it('renders Hindi with a Hindi lang attribute and no English dictionary copy', async () => {
    const { container } = await renderPage({ language: 'hi' })

    expect(container.querySelector('main')).toHaveAttribute('lang', 'hi')
    expectNoEnglishStrings(container.textContent ?? '')
    expect(container.innerHTML).not.toContain(getStrings('en').signInIntro)
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(getStrings('hi').signInHeading)
  })

  it('is a 404 in Mode A', async () => {
    mode.enabled = false

    await expect(SignupPage({ searchParams: Promise.resolve({}) })).rejects.toMatchObject({
      digest: 'NEXT_HTTP_ERROR_FALLBACK;404',
    })
  })
})
