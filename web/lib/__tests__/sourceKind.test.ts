import { describe, expect, it } from 'vitest'
import { classifySource, sourceHost } from '../sourceKind'

describe('sourceHost', () => {
  it('returns the bare hostname so the reader can see where a link goes', () => {
    expect(sourceHost('https://www.indiacode.nic.in/handle/123456789/1')).toBe(
      'indiacode.nic.in',
    )
  })

  it('returns null for a value that is not a URL at all', () => {
    expect(sourceHost('not a url')).toBeNull()
  })
})

describe('classifySource', () => {
  it('classifies an India Code URL as a statute', () => {
    expect(
      classifySource({
        chunk_id: 'a',
        source_url: 'https://www.indiacode.nic.in/handle/123456789/2385',
        title: 'Model Tenancy Act',
      }),
    ).toBe('statute')
  })

  it('classifies an India Code URL with no title as a statute, on the host alone', () => {
    expect(
      classifySource({
        chunk_id: 'a',
        source_url: 'https://indiacode.nic.in/handle/1',
        title: null,
      }),
    ).toBe('statute')
  })

  it('classifies an untitled judgment-archive URL as a judgment', () => {
    // The real shape of a `sc_judgments_v1` citation: the API sends
    // `title: null` (see `openlaw.api.schemas.SourceCard`) and a tarball URL.
    expect(
      classifySource({
        chunk_id: 'b',
        source_url:
          'https://indian-supreme-court-judgments.s3.amazonaws.com/data/tar/1963_INSC_244.tar',
        title: null,
      }),
    ).toBe('judgment')
  })

  it('does not claim a titled non-statute source is a judgment', () => {
    // Conservative on purpose: "judgment" is only claimed where the corpus
    // makes it certain (an untitled, non-India-Code citation). Anything else
    // gets the neutral label rather than a guess.
    expect(
      classifySource({
        chunk_id: 'c',
        source_url: 'https://egazette.gov.in/notification/1',
        title: 'Some notification',
      }),
    ).toBe('document')
  })

  it('falls back to the neutral kind when the URL cannot be parsed', () => {
    expect(
      classifySource({ chunk_id: 'd', source_url: 'not a url', title: 'Titled' }),
    ).toBe('document')
  })
})
