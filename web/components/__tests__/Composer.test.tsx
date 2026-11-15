import { createRef } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import Composer from '../Composer'
import { getStrings } from '@/lib/strings'

const strings = getStrings('en')

function renderComposer(overrides: Partial<React.ComponentProps<typeof Composer>> = {}) {
  const props: React.ComponentProps<typeof Composer> = {
    language: 'en',
    onLanguageChange: vi.fn(),
    question: 'A question',
    onQuestionChange: vi.fn(),
    onSend: vi.fn(),
    isLoading: false,
    textareaRef: createRef<HTMLTextAreaElement>(),
    ...overrides,
  }
  render(<Composer {...props} />)
  return props
}

describe('Composer', () => {
  it('sends on Enter and not on Shift+Enter', () => {
    const props = renderComposer()
    const box = screen.getByLabelText(strings.questionLabel)

    fireEvent.keyDown(box, { key: 'Enter', shiftKey: true })
    expect(props.onSend).not.toHaveBeenCalled()

    fireEvent.keyDown(box, { key: 'Enter' })
    expect(props.onSend).toHaveBeenCalledTimes(1)
  })

  it('never sends mid-composition', () => {
    const props = renderComposer()
    fireEvent.keyDown(screen.getByLabelText(strings.questionLabel), {
      key: 'Enter',
      isComposing: true,
    })
    expect(props.onSend).not.toHaveBeenCalled()
  })

  it('disables the send button while the box is empty or blank', () => {
    renderComposer({ question: '   ' })
    expect(screen.getByRole('button', { name: strings.askButton })).toBeDisabled()
  })

  it('disables the send button, and names it as asking, while loading', () => {
    renderComposer({ isLoading: true })
    expect(screen.getByRole('button', { name: strings.askingButton })).toBeDisabled()
  })

  it('does not call onSend from Enter while loading', () => {
    const props = renderComposer({ isLoading: true })
    fireEvent.keyDown(screen.getByLabelText(strings.questionLabel), { key: 'Enter' })
    expect(props.onSend).not.toHaveBeenCalled()
  })

  it('submits through the form so the button and Enter share one path', async () => {
    const props = renderComposer()
    await userEvent.click(screen.getByRole('button', { name: strings.askButton }))
    expect(props.onSend).toHaveBeenCalledTimes(1)
  })

  it('keeps the no-JavaScript GET fallback, with the language field attached to it', () => {
    renderComposer()
    const form = screen.getByLabelText(strings.questionLabel).closest('form')
    expect(form).toHaveAttribute('method', 'get')
    expect((screen.getByRole('combobox') as HTMLSelectElement).form).toBe(form)
    expect(screen.getByLabelText(strings.questionLabel)).toHaveAttribute('name', 'question')
  })

  it('renders its own labels in Hindi', () => {
    renderComposer({ language: 'hi' })
    const hi = getStrings('hi')
    expect(screen.getByLabelText(hi.questionLabel)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: hi.askButton })).toBeInTheDocument()
  })
})
