/** Where the FastAPI backend listens when `OPENLAW_API_URL` is unset (local
 * development). The one definition: the proxy, the server-initiated calls and
 * the mode-agreement check all read it from here. */
export const DEFAULT_API_URL = "http://127.0.0.1:8000";

/** The backend base URL, without a trailing slash. Server-side only. */
export function backendBaseUrl(override?: string): string {
  return (override ?? process.env.OPENLAW_API_URL ?? DEFAULT_API_URL).replace(/\/+$/, "");
}
