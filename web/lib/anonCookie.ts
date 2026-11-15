/**
 * The anonymous-identity cookie the backend mints (`openlaw.api.app
 * .ANON_COOKIE_NAME`, pinned by `lib/__tests__/backendParity.test.ts`). It owns
 * anonymous conversations and documents, so the frontend forwards it to the
 * backend and clears it on sign-out.
 */
export const ANON_COOKIE_NAME = "openlaw_anon_id";
