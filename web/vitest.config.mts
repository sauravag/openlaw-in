import path from 'node:path'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// `.mts` (not `.ts`) so Node loads this as a real ES module -- package.json
// deliberately has no `"type": "module"` (Next.js's own tooling expects
// CommonJS there), and Vite's native config loader otherwise warns on every
// run about ESM syntax in a CommonJS-loaded file.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
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
    },
  },
})
