import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import RefusalNotice from '../RefusalNotice'
import { formatResetDate } from '@/lib/formatDate'
import type { RefusalData } from '@/lib/types'

const baseRefusal: RefusalData = {
  detail: 'Free allowance used for this period.',
  quota_remaining: 0,
  signup_required: true,
  escalation_footer: 'Call NALSA free legal aid: 15100.',
  crisis_response: null,
  helplines: [{ number: '15100', label: 'Free legal aid (NALSA)' }],
  quota_reset_at: '2026-10-01',
  signup_url: 'https://openlaw.in/signup',
}

// The app.py 500 handler's body for "my husband beats me" -- no quota
// fields at all (Task 6 review, C1), but crisis_response and its structured
// helplines still present, exactly as `_unhandled_error_content` sends them.
const serverErrorCrisisRefusal: RefusalData = {
  detail: 'Something went wrong on our end. Please try again.',
  escalation_footer: 'Call NALSA free legal aid: 15100.',
  crisis_response: 'If you are in danger right now, call 112.',
  helplines: [
    { number: '112', label: 'Emergency' },
    { number: '181', label: "Women's helpline" },
    { number: '1091', label: "Police women's helpline" },
    { number: '15100', label: 'Free legal aid (NALSA)' },
  ],
}

describe('RefusalNotice', () => {
  it('always shows the escalation footer', () => {
    render(<RefusalNotice data={baseRefusal} language="en" />)
    expect(screen.getAllByText(/15100/).length).toBeGreaterThan(0)
  })

  it('surfaces the quota reset date, formatted for the locale (L2)', () => {
    render(<RefusalNotice data={baseRefusal} language="en" />)
    expect(screen.getByText(formatResetDate('2026-10-01', 'en'))).toBeInTheDocument()
  })

  it('shows the signup link when signup is required', () => {
    render(<RefusalNotice data={baseRefusal} language="en" />)
    expect(screen.getByRole('link', { name: /account/i })).toHaveAttribute(
      'href',
      'https://openlaw.in/signup',
    )
  })

  it('does not show a signup link when signup is not required', () => {
    render(<RefusalNotice data={{ ...baseRefusal, signup_required: false }} language="en" />)
    expect(screen.queryByRole('link', { name: /account/i })).not.toBeInTheDocument()
  })

  it('renders the crisis response above the refusal detail', () => {
    const { container } = render(
      <RefusalNotice
        data={{ ...baseRefusal, crisis_response: 'Call 181 for help now.' }}
        language="en"
      />,
    )
    const text = container.textContent ?? ''
    expect(text.indexOf('Call 181')).toBeLessThan(text.indexOf('Free allowance'))
  })

  it('marks the crisis response as an alert region', () => {
    render(
      <RefusalNotice
        data={{ ...baseRefusal, crisis_response: 'Call 181 for help now.' }}
        language="en"
      />,
    )
    expect(screen.getByRole('alert')).toHaveTextContent('Call 181')
  })

  it('omits the reset date when the refusal is a rate limit, not a quota', () => {
    render(<RefusalNotice data={{ ...baseRefusal, quota_reset_at: null }} language="en" />)
    expect(screen.queryByText(/resets/i)).not.toBeInTheDocument()
  })

  it('renders a 500-shaped refusal (no quota fields) without crashing or showing a quota block (C1)', () => {
    render(
      <RefusalNotice
        data={{ ...serverErrorCrisisRefusal, crisis_response: null, helplines: [] }}
        language="en"
      />,
    )
    expect(screen.getByText(/something went wrong/i)).toBeInTheDocument()
    expect(screen.getAllByText(/15100/).length).toBeGreaterThan(0)
    expect(screen.queryByText(/free questions left/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /account/i })).not.toBeInTheDocument()
  })

  it('renders a 500 body carrying a crisis response and its tap-to-call helplines (C1 enforcing test)', () => {
    // This is the exact defect class the fix round exists for: a prior
    // version discarded crisis_response entirely for any status other than
    // 429, so a woman typing "my husband beats me" into a momentarily
    // broken deployment saw only "Something went wrong." This asserts the
    // opposite: the crisis box, and its tel: links, actually reach the DOM.
    render(<RefusalNotice data={serverErrorCrisisRefusal} language="en" />)

    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('If you are in danger right now, call 112.')
    expect(alert.querySelector('a[href="tel:112"]')).toHaveTextContent('Emergency: 112')
    expect(alert.querySelector('a[href="tel:181"]')).toHaveTextContent("Women's helpline: 181")

    const text = document.body.textContent ?? ''
    expect(text.indexOf('If you are in danger')).toBeLessThan(
      text.indexOf('Something went wrong'),
    )
  })

  it('renders its own labels in Hindi when language="hi"', () => {
    render(<RefusalNotice data={baseRefusal} language="hi" />)
    expect(screen.getByText('शेष निःशुल्क सवाल')).toBeInTheDocument()
    expect(screen.getByText('अगली बार कब मिलेगा')).toBeInTheDocument()
  })
})
