import { afterEach, describe, expect, it } from 'vitest'
import {
  CONVERSATION_STORAGE_KEY,
  forgetStoredConversationId,
  readStoredConversation,
  storeConversationId,
} from '../thread'
import { restoreFailureAction } from '../useRestoreConversation'

describe('stored conversation pointer', () => {
  afterEach(() => window.sessionStorage.clear())

  it('remembers whether the conversation was stored while signed in', () => {
    storeConversationId('conv-1', true)
    expect(readStoredConversation()).toEqual({ id: 'conv-1', storedWhileSignedIn: true })

    storeConversationId('conv-2', false)
    expect(readStoredConversation()).toEqual({ id: 'conv-2', storedWhileSignedIn: false })
  })

  it('forgets both the id and the flag', () => {
    storeConversationId('conv-1', true)
    forgetStoredConversationId()
    expect(readStoredConversation()).toBeNull()
    expect(window.sessionStorage.length).toBe(0)
  })

  it('reads an id stored by an older build as anonymous', () => {
    window.sessionStorage.setItem(CONVERSATION_STORAGE_KEY, 'conv-old')
    expect(readStoredConversation()).toEqual({ id: 'conv-old', storedWhileSignedIn: false })
  })
})

describe('restoreFailureAction (final review H1a)', () => {
  it('Mode A: forgets on any failure, unchanged', () => {
    for (const status of [0, 404, 500]) {
      expect(
        restoreFailureAction({ authEnabled: false, status, signedIn: false, storedWhileSignedIn: true }),
      ).toBe('forget')
    }
  })

  it('Mode B: forgets only a genuine 404 while a valid session is present', () => {
    expect(
      restoreFailureAction({ authEnabled: true, status: 404, signedIn: true, storedWhileSignedIn: true }),
    ).toBe('forget')
    expect(
      restoreFailureAction({ authEnabled: true, status: 502, signedIn: true, storedWhileSignedIn: true }),
    ).toBe('keep')
    expect(
      restoreFailureAction({ authEnabled: true, status: 0, signedIn: true, storedWhileSignedIn: false }),
    ).toBe('keep')
  })

  it('Mode B: a lapsed session keeps a kept conversation and says so', () => {
    for (const status of [0, 401, 404, 502]) {
      expect(
        restoreFailureAction({ authEnabled: true, status, signedIn: false, storedWhileSignedIn: true }),
      ).toBe('sessionEnded')
    }
  })

  it('Mode B: an anonymous conversation that expired is forgotten, as in Mode A', () => {
    expect(
      restoreFailureAction({ authEnabled: true, status: 404, signedIn: false, storedWhileSignedIn: false }),
    ).toBe('forget')
    expect(
      restoreFailureAction({ authEnabled: true, status: 0, signedIn: false, storedWhileSignedIn: false }),
    ).toBe('keep')
  })
})
