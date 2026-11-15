// @vitest-environment node
import { describe, expect, it } from 'vitest'
import { hintLanguage, isFormPost, parseFormInput } from '../formInput'

const FORM = 'application/x-www-form-urlencoded'

function request(headers: Record<string, string>): Request {
  return new Request('http://web/x', { method: 'POST', headers })
}

describe('shared auth form helpers (final review L3)', () => {
  it('parses the named fields and the language from a no-JavaScript form', () => {
    expect(parseFormInput('email=a%40b.in&language=hi&other=x', FORM, ['email'])).toEqual({
      fields: { email: 'a@b.in' },
      language: 'hi',
    })
  })

  it('parses the named fields and the language from JSON', () => {
    expect(
      parseFormInput('{"confirmation":"DELETE","language":"fr"}', 'application/json', ['confirmation']),
    ).toEqual({ fields: { confirmation: 'DELETE' }, language: 'en' })
  })

  it('returns null for unparseable or non-object JSON', () => {
    expect(parseFormInput('{nope', 'application/json', ['email'])).toBeNull()
    expect(parseFormInput('"x"', 'application/json', ['email'])).toBeNull()
    expect(parseFormInput('null', 'application/json', ['email'])).toBeNull()
  })

  it('reads a missing field as null from a form and undefined-free from JSON', () => {
    expect(parseFormInput('', FORM, ['email'])).toEqual({ fields: { email: null }, language: 'en' })
    expect(parseFormInput('{}', 'application/json', ['email'])).toEqual({
      fields: { email: undefined },
      language: 'en',
    })
  })

  it('tells a form post from anything else', () => {
    expect(isFormPost(request({ 'content-type': `${FORM}; charset=UTF-8` }))).toBe(true)
    expect(isFormPost(request({ 'content-type': 'application/json' }))).toBe(false)
    expect(isFormPost(request({}))).toBe(false)
  })

  it('hints Hindi only from a Hindi Accept-Language', () => {
    expect(hintLanguage(request({ 'accept-language': 'hi-IN,hi;q=0.9' }))).toBe('hi')
    expect(hintLanguage(request({ 'accept-language': ' HI' }))).toBe('hi')
    expect(hintLanguage(request({ 'accept-language': 'en-IN' }))).toBe('en')
    expect(hintLanguage(request({}))).toBe('en')
  })
})
