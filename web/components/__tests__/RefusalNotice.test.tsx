import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import RefusalNotice from '../RefusalNotice'
import type { RefusalData } from '@/lib/types'

const baseRefusal: RefusalData = {
  detail: 'Free allowance used for this period.',
  quota_remaining: 0,
  signup_required: true,
  escalation_footer: 'Call NALSA free legal aid: 15100.',
  crisis_response: null,
  quota_reset_at: '2026-10-01',
  signup_url: 'https://openlaw.in/signup',
}

describe('RefusalNotice', () => {
  it('always shows the escalation footer', () => {
    render(<RefusalNotice data={baseRefusal} />)
    expect(screen.getByText(/15100/)).toBeInTheDocument()
  })

  it('surfaces the quota reset date', () => {
    render(<RefusalNotice data={baseRefusal} />)
    expect(screen.getByText(/2026-10-01/)).toBeInTheDocument()
  })

  it('shows the signup link when signup is required', () => {
    render(<RefusalNotice data={baseRefusal} />)
    expect(screen.getByRole('link', { name: /account/i })).toHaveAttribute(
      'href',
      'https://openlaw.in/signup',
    )
  })

  it('does not show a signup link when signup is not required', () => {
    render(<RefusalNotice data={{ ...baseRefusal, signup_required: false }} />)
    expect(screen.queryByRole('link', { name: /account/i })).not.toBeInTheDocument()
  })

  it('renders the crisis response above the refusal detail', () => {
    const { container } = render(
      <RefusalNotice
        data={{ ...baseRefusal, crisis_response: 'Call 181 for help now.' }}
      />,
    )
    const text = container.textContent ?? ''
    expect(text.indexOf('Call 181')).toBeLessThan(text.indexOf('Free allowance'))
  })

  it('marks the crisis response as an alert region', () => {
    render(
      <RefusalNotice
        data={{ ...baseRefusal, crisis_response: 'Call 181 for help now.' }}
      />,
    )
    expect(screen.getByRole('alert')).toHaveTextContent('Call 181')
  })

  it('omits the reset date when the refusal is a rate limit, not a quota', () => {
    render(<RefusalNotice data={{ ...baseRefusal, quota_reset_at: null }} />)
    expect(screen.queryByText(/resets/i)).not.toBeInTheDocument()
  })
})
