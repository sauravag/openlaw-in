/**
 * PKCE (RFC 7636) helpers. Pure Web Crypto -- no dependency, runs in Node and
 * the edge runtime. Used server-side only; the verifier never reaches
 * client-side script (it lives in an HttpOnly cookie).
 */

/** 32 random bytes encode to 43 base64url characters, the RFC minimum, with
 * 256 bits of entropy. */
const VERIFIER_BYTES = 32;

function base64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** A fresh verifier from the CSPRNG; nothing about it is derived from
 * anything guessable. */
export function createVerifier(): string {
  const bytes = new Uint8Array(VERIFIER_BYTES);
  crypto.getRandomValues(bytes);
  return base64Url(bytes);
}

/** `code_challenge_method=S256`: BASE64URL(SHA-256(ASCII(verifier))). */
export async function challengeFor(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(verifier),
  );
  return base64Url(new Uint8Array(digest));
}
