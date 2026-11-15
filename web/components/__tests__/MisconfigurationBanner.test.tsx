import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import MisconfigurationBanner from '../MisconfigurationBanner'
import { ANONYMOUS_UPLOAD_TTL_HOURS } from '@/lib/documents'
import { getStrings } from '@/lib/strings'

describe('MisconfigurationBanner (final review H3c)', () => {
  it('says the deployment is misconfigured and uploads may be deleted, as an alert', () => {
    render(<MisconfigurationBanner language="en" />)

    const banner = screen.getByRole('alert')
    expect(banner).toHaveTextContent(getStrings('en').misconfiguredBanner(ANONYMOUS_UPLOAD_TTL_HOURS))
    expect(banner.textContent).toMatch(/misconfigured/i)
    expect(banner.textContent).toContain(`${ANONYMOUS_UPLOAD_TTL_HOURS} hours`)
  })

  it('is in Hindi for a Hindi reader', () => {
    render(<MisconfigurationBanner language="hi" />)

    expect(screen.getByRole('alert')).toHaveTextContent(
      getStrings('hi').misconfiguredBanner(ANONYMOUS_UPLOAD_TTL_HOURS),
    )
  })
})
