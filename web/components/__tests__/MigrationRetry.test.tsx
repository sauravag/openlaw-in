import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import MigrationRetry from '../MigrationRetry'
import { retryMigration } from '@/lib/api'
import { getStrings } from '@/lib/strings'

vi.mock('@/lib/authMode', () => ({ AUTH_ENABLED: true }))
vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api')
  return { ...actual, retryMigration: vi.fn() }
})
const mockedRetry = vi.mocked(retryMigration)

const refusal = {
  detail: getStrings('en').authMigrationFailed,
  escalation_footer: 'Call NALSA free legal aid: 15100.',
  crisis_response: null,
  helplines: [{ number: '15100', label: 'Free legal aid (NALSA)' }],
}

describe('MigrationRetry (final review H2)', () => {
  beforeEach(() => mockedRetry.mockReset())

  it('states the true situation and offers a retry', () => {
    render(<MigrationRetry language="en" onMigrated={() => undefined} />)

    const strings = getStrings('en')
    expect(screen.getByText(strings.authMigrationFailed)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: strings.migrationRetryButton })).toBeEnabled()
  })

  it('reports success and tells the page', async () => {
    mockedRetry.mockResolvedValue({ kind: 'migrated' })
    const onMigrated = vi.fn()
    render(<MigrationRetry language="en" onMigrated={onMigrated} />)

    await userEvent.click(screen.getByRole('button', { name: getStrings('en').migrationRetryButton }))

    expect(await screen.findByText(getStrings('en').migrationSucceeded)).toBeInTheDocument()
    expect(screen.queryByText(getStrings('en').authMigrationFailed)).not.toBeInTheDocument()
    expect(onMigrated).toHaveBeenCalledTimes(1)
    expect(mockedRetry).toHaveBeenCalledWith({ language: 'en' })
  })

  it('reports a failure with its help, and keeps the retry', async () => {
    mockedRetry.mockResolvedValue({ kind: 'refused', status: 502, data: refusal })
    render(<MigrationRetry language="en" onMigrated={() => undefined} />)

    await userEvent.click(screen.getByRole('button', { name: getStrings('en').migrationRetryButton }))

    expect(await screen.findByRole('link', { name: /15100/ })).toHaveAttribute('href', 'tel:15100')
    expect(screen.getByRole('button', { name: getStrings('en').migrationRetryButton })).toBeEnabled()
  })

  it('is in Hindi throughout', () => {
    render(<MigrationRetry language="hi" onMigrated={() => undefined} />)

    expect(screen.getByText(getStrings('hi').authMigrationFailed)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: getStrings('hi').migrationRetryButton })).toBeInTheDocument()
  })
})
