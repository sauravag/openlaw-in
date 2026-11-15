import { describe, expect, it } from "vitest";

import { accountDeletedNotice } from "../accountDeletedStrings";

describe("accountDeletedNotice", () => {
  it("names the erased material and says the access log survives without content (en)", () => {
    const text = accountDeletedNotice("en", true);
    expect(text).toMatch(/conversations, uploaded documents and excerpts have been erased/);
    expect(text).toMatch(/access log\) is kept, without any document content/);
  });

  it("says identity removed only when it was, and says it may remain otherwise", () => {
    expect(accountDeletedNotice("en", true)).toMatch(/has also been removed/);
    expect(accountDeletedNotice("en", true)).not.toMatch(/may remain/);
    expect(accountDeletedNotice("en", false)).toMatch(/could not be removed and may remain/);
    expect(accountDeletedNotice("en", false)).not.toMatch(/has also been removed/);
  });

  it("is available in Hindi with the same facts", () => {
    expect(accountDeletedNotice("hi", true)).toMatch(/एक्सेस लॉग/);
    expect(accountDeletedNotice("hi", true)).toMatch(/हटा दी गई है/);
    expect(accountDeletedNotice("hi", false)).toMatch(/बनी रह सकती है/);
    expect(accountDeletedNotice("hi", false)).not.toMatch(/हटा दी गई है/);
  });
});
