import { vi } from 'vitest'

const FLAG = 'NEXT_PUBLIC_OPENLAW_AUTH_ENABLED'

/**
 * `AUTH_ENABLED` is read once, at module load, from a build-time constant, so
 * switching mode inside a test means switching the variable AND discarding
 * every module that already captured the old value. Doing only the first
 * asserts against a stale constant (a test that passes in the wrong mode).
 * This does both; `vitest.setup.ts` restores the variable after each test.
 */
export function setAuthMode(authEnabled: boolean): void {
  vi.resetModules()
  vi.stubEnv(FLAG, authEnabled ? 'true' : 'false')
}

/** Switch mode, then import `specifier` fresh so it sees that mode. */
export async function importInMode<T>(
  authEnabled: boolean,
  specifier: () => Promise<T>,
): Promise<T> {
  setAuthMode(authEnabled)
  return specifier()
}

/** The mode the whole vitest run was started in (the flag as the build sees it). */
export function runMode(): 'a' | 'b' {
  return process.env[FLAG] === 'true' ? 'b' : 'a'
}
