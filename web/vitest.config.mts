import path from 'node:path'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// The run's build mode is the build-time flag itself. Anything other than an
// exact "true"/"false" would silently mean Mode A (`lib/authMode.ts`), which
// is how a typo in CI becomes a half-tested flag, so refuse it here.
const MODE_FLAG = process.env.NEXT_PUBLIC_OPENLAW_AUTH_ENABLED
if (MODE_FLAG !== undefined && MODE_FLAG !== 'true' && MODE_FLAG !== 'false') {
  throw new Error(
    `NEXT_PUBLIC_OPENLAW_AUTH_ENABLED must be "true" or "false" (or unset), got ${JSON.stringify(MODE_FLAG)}`,
  )
}

// CI sets a marker directory; the reporter then records each mode's run.
const reporters = process.env.OPENLAW_TEST_MODE_MARKER_DIR
  ? ['default', './test-utils/modeMarkerReporter.mjs']
  : ['default']

// `.mts` (not `.ts`) so Node loads this as a real ES module -- package.json
// deliberately has no `"type": "module"` (Next.js's own tooling expects
// CommonJS there), and Vite's native config loader otherwise warns on every
// run about ESM syntax in a CommonJS-loaded file.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    reporters,
    setupFiles: ['./vitest.setup.ts'],
    include: ['**/__tests__/**/*.test.{ts,tsx}'],
    exclude: ['node_modules', '.next'],
    // Matches the Python side's `--cov-fail-under=80` (see pyproject.toml)
    // -- one coverage floor for the whole project, not a lower bar just
    // because this half is TypeScript.
    coverage: {
      provider: 'v8',
      include: ['lib/**/*.{ts,tsx}', 'components/**/*.tsx', 'app/**/*.tsx'],
      exclude: ['**/__tests__/**'],
      thresholds: {
        statements: 80,
        branches: 80,
        functions: 80,
        lines: 80,
      },
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, '.'),
      // Next aliases `server-only` itself at build time (the package is not
      // installed); under vitest there is no such alias, and the real module
      // throws by design, so point it at Next's own no-op twin.
      'server-only': path.resolve(
        import.meta.dirname,
        'node_modules/next/dist/compiled/server-only/empty.js',
      ),
    },
  },
})
