import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import AnswerView from '../AnswerView'
import type { AnswerData } from '@/lib/types'

const baseAnswer: AnswerData = {
  answer: 'The deposit must be refunded within one month.',
  sources: [
    {
      chunk_id: 'a',
      source_url: 'https://www.indiacode.nic.in/handle/123456789/1',
      title: 'Model Tenancy Act',
    },
  ],
  language: 'en',
  corpus_version: 'v2026.08',
  footer: 'This is legal information, not legal advice. NALSA: 15100',
  crisis_response: null,
  is_answerable: true,
  quota_remaining: 29,
}

describe('AnswerView', () => {
  it('shows the answer text', () => {
    render(<AnswerView data={baseAnswer} />)
    expect(screen.getByText(/deposit must be refunded/i)).toBeInTheDocument()
  })

  it('renders every source as a link to the government document', () => {
    render(<AnswerView data={baseAnswer} />)
    expect(
      screen.getByRole('link', { name: /Model Tenancy Act/i }),
    ).toHaveAttribute('href', 'https://www.indiacode.nic.in/handle/123456789/1')
  })

  it('shows the corpus version so the answer is visibly time-bound', () => {
    render(<AnswerView data={baseAnswer} />)
    expect(screen.getByText(/v2026\.08/)).toBeInTheDocument()
  })

  it('always shows the legal-aid escalation footer', () => {
    render(<AnswerView data={baseAnswer} />)
    expect(screen.getByText(/15100/)).toBeInTheDocument()
  })

  it('renders the crisis response above the answer', () => {
    const { container } = render(
      <AnswerView
        data={{ ...baseAnswer, crisis_response: 'Call 181 for help now.' }}
      />,
    )
    const text = container.textContent ?? ''
    expect(text.indexOf('Call 181')).toBeLessThan(text.indexOf('deposit must be'))
  })

  it('marks the crisis response as an alert region for screen readers', () => {
    render(
      <AnswerView
        data={{ ...baseAnswer, crisis_response: 'Call 181 for help now.' }}
      />,
    )
    expect(screen.getByRole('alert')).toHaveTextContent('Call 181')
  })

  it('shows a clear message when no answer could be grounded', () => {
    render(<AnswerView data={{ ...baseAnswer, is_answerable: false, sources: [] }} />)
    expect(screen.getByTestId('unanswerable')).toBeInTheDocument()
  })

  it('omits the sources section entirely when there are no sources', () => {
    render(<AnswerView data={{ ...baseAnswer, sources: [] }} />)
    expect(screen.queryByRole('heading', { name: /where this comes from/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /model tenancy act/i })).not.toBeInTheDocument()
  })
})
