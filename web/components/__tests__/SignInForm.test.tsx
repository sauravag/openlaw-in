import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getStrings } from '@/lib/strings'

const mode = vi.hoisted(() => ({ enabled: true }))
vi.mock('@/lib/authMode', () => ({
  get AUTH_ENABLED() {
    return mode.enabled
  },
}))

import SignInForm from '../SignInForm'

beforeEach(() => {
  mode.enabled = true
})

describe('SignInForm', () => {
  it('is a real post form to the sign-in route with a named email input', () => {
    const { container } = render(<SignInForm language="en" />)

    const form = container.querySelector('form')
    expect(form).toHaveAttribute('method', 'post')
    expect(form).toHaveAttribute('action', '/api/auth/sign-in')
    const email = screen.getByLabelText(getStrings('en').signInEmailLabel)
    expect(email).toHaveAttribute('name', 'email')
  })

  it('asks a mobile keyboard for an email layout', () => {
    render(<SignInForm language="en" />)

    const email = screen.getByLabelText(getStrings('en').signInEmailLabel)
    expect(email).toHaveAttribute('type', 'email')
    expect(email).toHaveAttribute('autocomplete', 'email')
    expect(email).toHaveAttribute('inputmode', 'email')
    expect(email).toBeRequired()
  })

  it.each(['en', 'hi'])('posts the %s language as a hidden field', (language) => {
    const { container } = render(<SignInForm language={language} />)

    const hidden = container.querySelector('input[name="language"]')
    expect(hidden).toHaveAttribute('type', 'hidden')
    expect(hidden).toHaveAttribute('value', language)
  })

  it('renders no English string from the dictionary in the Hindi tree', () => {
    const { container } = render(<SignInForm language="hi" />)
    const en = getStrings('en')

    const text = container.innerHTML
    for (const english of [en.signInEmailLabel, en.signInSubmit]) {
      expect(text).not.toContain(english)
    }
    expect(screen.getByRole('button')).toHaveTextContent(getStrings('hi').signInSubmit)
  })

  it('renders nothing in Mode A', () => {
    mode.enabled = false

    const { container } = render(<SignInForm language="en" />)

    expect(container).toBeEmptyDOMElement()
  })
})
