import { mkdirSync, writeFileSync } from 'node:fs'
import path from 'node:path'

/**
 * Writes `vitest-<mode>.json` when a run ends, so CI's last step
 * (`scripts/check_modes_ran.py`) can prove each mode ran and passed. The
 * Python twin is `tests/conftest.py::pytest_sessionfinish`. Active only when
 * the run is configured with this reporter (see vitest.config.mts), i.e. when
 * OPENLAW_TEST_MODE_MARKER_DIR is set.
 */
export default class ModeMarkerReporter {
  onTestRunEnd(testModules, unhandledErrors, reason) {
    const mode = process.env.NEXT_PUBLIC_OPENLAW_AUTH_ENABLED === 'true' ? 'b' : 'a'
    const tests = testModules.reduce((n, mod) => n + [...mod.children.allTests()].length, 0)
    const directory = process.env.OPENLAW_TEST_MODE_MARKER_DIR
    if (!directory) return
    mkdirSync(directory, { recursive: true })
    writeFileSync(
      path.join(directory, `vitest-${mode}.json`),
      JSON.stringify({
        suite: 'vitest',
        mode,
        passed: reason === 'passed' && unhandledErrors.length === 0,
        tests,
      }),
    )
  }
}
