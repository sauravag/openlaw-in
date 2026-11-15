// @vitest-environment node
import { readdirSync, readFileSync, statSync } from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'
import { ANON_COOKIE_NAME } from '../anonCookie'
import { ANONYMOUS_UPLOAD_TTL_HOURS } from '../documents'

/**
 * Final review L3: constants the frontend mirrors from the backend are pinned
 * to the backend SOURCE, and the ones defined twice in the frontend are
 * defined once.
 */
const WEB_ROOT = path.resolve(import.meta.dirname, '..', '..')
const REPO_ROOT = path.resolve(WEB_ROOT, '..')
const read = (relative: string) => readFileSync(path.join(REPO_ROOT, relative), 'utf8')

/** `NAME = <value>` or `name: type = <value>` from a Python source file. */
function pythonValue(source: string, pattern: RegExp): string {
  const match = source.match(pattern)
  if (!match) throw new Error(`pattern not found: ${pattern}`)
  return match[1]
}

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name)
    if (name === 'node_modules' || name === '.next' || name === '__tests__') return []
    if (statSync(full).isDirectory()) return sourceFiles(full)
    return /\.(ts|tsx|mjs)$/.test(name) ? [full] : []
  })
}

const FRONTEND_SOURCES = ['app', 'lib', 'components']
  .flatMap((dir) => sourceFiles(path.join(WEB_ROOT, dir)))
  .concat([path.join(WEB_ROOT, 'instrumentation.ts')])

function filesContaining(needle: RegExp): string[] {
  return FRONTEND_SOURCES.filter((file) => needle.test(readFileSync(file, 'utf8')))
    .map((file) => path.relative(WEB_ROOT, file))
    .sort()
}

describe('frontend constants pinned to the backend', () => {
  it('uses the backend anonymous cookie name', () => {
    const backend = pythonValue(read('src/openlaw/api/app.py'), /^ANON_COOKIE_NAME = "([^"]+)"/m)
    expect(ANON_COOKIE_NAME).toBe(backend)
  })

  it('states the backend anonymous upload lifetime', () => {
    const hours = pythonValue(
      read('src/openlaw/config.py'),
      /^\s+anonymous_upload_ttl_hours: int = (\d+)/m,
    )
    expect(ANONYMOUS_UPLOAD_TTL_HOURS).toBe(Number(hours))
  })
})

describe('defined once in the frontend', () => {
  it('has one default backend URL', () => {
    expect(filesContaining(/127\.0\.0\.1:8000/)).toEqual(['lib/apiUrl.ts'])
  })

  it('has one copy of each form helper', () => {
    for (const name of ['hintLanguage', 'isFormPost', 'parseFormInput']) {
      expect(filesContaining(new RegExp(`function ${name}\\b`)), name).toEqual([
        'lib/auth/formInput.ts',
      ])
    }
  })

  it('has one anonymous cookie name literal', () => {
    expect(filesContaining(/"openlaw_anon_id"|'openlaw_anon_id'/)).toEqual(['lib/anonCookie.ts'])
  })
})
