import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getStrings } from '@/lib/strings'

const mode = vi.hoisted(() => ({ enabled: true }))
vi.mock('@/lib/authMode', () => ({
  get AUTH_ENABLED() {
    return mode.enabled
  },
}))

import AccountBar from '../AccountBar'

beforeEach(() => {
  mode.enabled = true
})

describe('AccountBar', () => {
  it('shows the sign-in link when signed out', () => {
    render(<AccountBar signedIn={false} email={null} language="en" />)

    expect(screen.getByRole('link', { name: getStrings('en').signInCta })).toHaveAttribute(
      'href',
      '/signup',
    )
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('shows the email, a sign-out post form and the delete link when signed in', () => {
    const { container } = render(
      <AccountBar signedIn email="person@example.com" language="en" />,
    )

    expect(screen.getByText(/person@example\.com/)).toBeInTheDocument()
    expect(container.querySelector('form')).toHaveAttribute('action', '/api/auth/sign-out')
    expect(container.querySelector('form')).toHaveAttribute('method', 'post')
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Delete my account' })).toHaveAttribute(
      'href',
      '/account/delete',
    )
  })

  it('renders Hindi copy and carries the language into its links', () => {
    render(<AccountBar signedIn email="person@example.com" language="hi" />)

    const hi = getStrings('hi')
    expect(screen.getByRole('button', { name: hi.signOutButton })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: hi.deleteAccountLink })).toHaveAttribute(
      'href',
      '/account/delete?language=hi',
    )
    expect(screen.queryByText('Sign out')).toBeNull()
  })

  it('renders nothing at all in Mode A, signed in or out', () => {
    mode.enabled = false

    const out = render(<AccountBar signedIn={false} email={null} language="en" />)
    const inn = render(<AccountBar signedIn email="a@b.co" language="en" />)

    expect(out.container).toBeEmptyDOMElement()
    expect(inn.container).toBeEmptyDOMElement()
  })
})
