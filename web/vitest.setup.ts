import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'
import '@testing-library/jest-dom/vitest'

// `@testing-library/react`'s automatic cleanup only registers itself when it
// finds a *global* `afterEach` (the Jest-style convention) -- `test.globals`
// is deliberately left off in vitest.config.ts (explicit imports over
// ambient globals), so without this every test in a file would render into
// the same jsdom document and later assertions (e.g. `queryByText`) would
// see every previous test's markup too.
afterEach(() => {
  cleanup()
})
