import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import WelcomeGuide from '../WelcomeGuide'
import { getStrings } from '@/lib/strings'

describe('WelcomeGuide', () => {
  it('tells a first-time visitor what this is, that it is free, and that it cites sources', () => {
    render(<WelcomeGuide language="en" onPickExample={vi.fn()} />)

    expect(screen.getByRole('heading', { name: /what this is/i })).toBeInTheDocument()
    expect(screen.getByText(/free to use, with no account/i)).toBeInTheDocument()
    expect(screen.getByText(/every source is a link you can open yourself/i)).toBeInTheDocument()
  })

  it('is honest about what the corpus covers and does not cover', () => {
    render(<WelcomeGuide language="en" onPickExample={vi.fn()} />)

    const coverage = screen.getByText(/supreme court/i)
    expect(coverage).toHaveTextContent(/punjab & haryana high court/i)
    expect(coverage).toHaveTextContent(/delhi high court/i)
    expect(coverage).toHaveTextContent(/does not cover district courts/i)
  })

  it('says plainly that it is not a lawyer', () => {
    render(<WelcomeGuide language="en" onPickExample={vi.fn()} />)
    expect(screen.getByText(/not a lawyer and cannot give legal advice/i)).toBeInTheDocument()
  })

  it('offers the example questions as tappable buttons', () => {
    render(<WelcomeGuide language="en" onPickExample={vi.fn()} />)

    for (const question of getStrings('en').exampleQuestions) {
      expect(screen.getByRole('button', { name: question })).toBeInTheDocument()
    }
  })

  it('hands the chosen example back to the page rather than asking it directly', async () => {
    // Filling the box, not firing a request: a mis-tap must not silently
    // spend one of a person's free questions.
    const onPickExample = vi.fn()
    render(<WelcomeGuide language="en" onPickExample={onPickExample} />)

    const [first] = getStrings('en').exampleQuestions
    await userEvent.click(screen.getByRole('button', { name: first }))

    expect(onPickExample).toHaveBeenCalledWith(first)
  })

  it('renders entirely in Hindi when the language is hi, with no English copy', () => {
    render(<WelcomeGuide language="hi" onPickExample={vi.fn()} />)

    expect(screen.getByRole('heading', { name: 'यह क्या है' })).toBeInTheDocument()
    for (const question of getStrings('hi').exampleQuestions) {
      expect(screen.getByRole('button', { name: question })).toBeInTheDocument()
    }
    expect(screen.queryByText(/what this is/i)).not.toBeInTheDocument()
  })
})
