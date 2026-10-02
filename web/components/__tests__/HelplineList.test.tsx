import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import HelplineList from '../HelplineList'

describe('HelplineList', () => {
  it('renders each helpline as a tap-to-call link with its label', () => {
    render(
      <HelplineList
        helplines={[
          { number: '112', label: 'Emergency' },
          { number: '181', label: "Women's helpline" },
        ]}
      />,
    )
    expect(screen.getByRole('link', { name: /emergency.*112/i })).toHaveAttribute(
      'href',
      'tel:112',
    )
    expect(screen.getByRole('link', { name: /women's helpline.*181/i })).toHaveAttribute(
      'href',
      'tel:181',
    )
  })

  it('renders nothing when there are no helplines', () => {
    const { container } = render(<HelplineList helplines={[]} />)
    expect(container).toBeEmptyDOMElement()
  })
})
