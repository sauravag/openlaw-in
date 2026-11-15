/**
 * The spellings of `OPENLAW_AUTH_ENABLED` the backend's pydantic `bool` reads
 * as true, compared case-insensitively (final review H3a). Compose passes the
 * operator's raw value to both the API and the web build, so the frontend must
 * read it the same way or the two halves run in different modes. Pinned to
 * pydantic's behaviour by `tests/test_auth_flag_parity.py`; keep it a one-line
 * JSON array so that test can read it.
 */
export const TRUTHY_AUTH_FLAG_VALUES = ["true", "1", "yes", "on", "t", "y"];

/** Whether a raw flag value means "auth on". Anything else, unset included, is off. */
export function isAuthFlagOn(raw: string | undefined): boolean {
  return raw !== undefined && TRUTHY_AUTH_FLAG_VALUES.includes(raw.toLowerCase());
}
