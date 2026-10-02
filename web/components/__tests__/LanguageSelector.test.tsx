import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import LanguageSelector from '../LanguageSelector'

describe('LanguageSelector', () => {
  it('offers only English and Hindi', () => {
    render(<LanguageSelector value="en" onChange={vi.fn()} />)
    const options = screen.getAllByRole('option').map((option) => option.textContent)
    expect(options).toHaveLength(2)
    expect(options.some((label) => /english/i.test(label ?? ''))).toBe(true)
    expect(options.some((label) => /हिन्दी/.test(label ?? ''))).toBe(true)
  })

  it('does not offer any of the ten languages without a verified glossary', () => {
    render(<LanguageSelector value="en" onChange={vi.fn()} />)
    const options = screen.getAllByRole('option').map((option) => option.textContent ?? '')
    expect(options.some((label) => /bengali|tamil|telugu|marathi|gujarati|kannada|malayalam|punjabi|odia|assamese/i.test(label))).toBe(false)
  })

  it('calls onChange with the selected language code', async () => {
    const onChange = vi.fn()
    render(<LanguageSelector value="en" onChange={onChange} />)
    await userEvent.selectOptions(screen.getByRole('combobox'), 'hi')
    expect(onChange).toHaveBeenCalledWith('hi')
  })

  it('reflects the current value', () => {
    render(<LanguageSelector value="hi" onChange={vi.fn()} />)
    expect(screen.getByRole('combobox')).toHaveValue('hi')
  })
})
