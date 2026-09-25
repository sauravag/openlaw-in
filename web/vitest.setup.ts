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

// jsdom does not implement `scrollIntoView` at all -- calling it throws
// "not a function" (`app/page.tsx`'s `useScrollIntoViewOnChange`, Task 6
// review H2, calls it on every new result/error). A no-op stub is enough:
// this test environment only needs the call not to crash, not to actually
// move a viewport that does not exist.
if (typeof Element !== 'undefined' && !Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {}
}
