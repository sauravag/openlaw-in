import { AUTH_ENABLED } from "./authMode";
import { getFallbackHelplineSet } from "./fallbackHelplines";
import { getStrings } from "./strings";
import type { RefusalData } from "./types";

/**
 * Detects the two silent ways the build-time frontend flag and the run-time
 * backend setting can disagree:
 *
 * - frontend built WITH sign-in, backend auth OFF: a sign-in button that the
 *   backend knows nothing about;
 * - frontend built WITHOUT sign-in, backend auth ON: quota is enforced and
 *   refusals point at a sign-in that was never shipped.
 *
 * Both are configuration errors. This module makes them loud (one log line,
 * with the remedy) and keeps the anonymous product working: a mismatch
 * degrades the sign-in affordance, never the ability to ask a question.
 */

export const MISMATCH_MESSAGE =
  "OPENLAW_AUTH_ENABLED (backend) and NEXT_PUBLIC_OPENLAW_AUTH_ENABLED (frontend build) disagree; rebuild the frontend or change the backend setting";

const STATUS_TIMEOUT_MS = 3000;

export interface AuthAgreement {
  backendAuthEnabled: boolean | null;
  frontendAuthEnabled: boolean;
  /** `null` when the backend could not be asked: unreachable is not a mismatch. */
  agrees: boolean | null;
}

export interface AgreementOptions {
  fetchImpl?: typeof fetch;
  frontendEnabled?: boolean;
  apiUrl?: string;
}

// Process-lifetime latch: a permanent configuration error is logged once, not
// per request, so it cannot bury the next real problem.
let hasLoggedMismatch = false;

/** Test seam: clear the log-once latch. */
export function resetMismatchLog(): void {
  hasLoggedMismatch = false;
}

function apiBase(options: AgreementOptions): string {
  return (
    options.apiUrl ?? process.env.OPENLAW_API_URL ?? "http://127.0.0.1:8000"
  );
}

function logMismatchOnce(backend: boolean, frontend: boolean): void {
  if (hasLoggedMismatch) return;
  hasLoggedMismatch = true;
  const state = (on: boolean): string => (on ? "on" : "off");
  console.error(
    `[Auth] ${MISMATCH_MESSAGE} (backend: ${state(backend)}, frontend build: ${state(frontend)})`,
  );
}

async function readBackendAuthEnabled(
  options: AgreementOptions,
): Promise<boolean | null> {
  const fetchImpl = options.fetchImpl ?? fetch;
  try {
    const response = await fetchImpl(`${apiBase(options)}/auth/status`, {
      cache: "no-store",
      signal: AbortSignal.timeout(STATUS_TIMEOUT_MS),
    });
    if (!response.ok) return null;
    const body: unknown = await response.json();
    const flag = (body as { auth_enabled?: unknown }).auth_enabled;
    return typeof flag === "boolean" ? flag : null;
  } catch {
    return null;
  }
}

export async function checkAuthAgreement(
  options: AgreementOptions = {},
): Promise<AuthAgreement> {
  const frontendAuthEnabled = options.frontendEnabled ?? AUTH_ENABLED;
  const backendAuthEnabled = await readBackendAuthEnabled(options);
  if (backendAuthEnabled === null) {
    return { backendAuthEnabled, frontendAuthEnabled, agrees: null };
  }
  const agrees = backendAuthEnabled === frontendAuthEnabled;
  if (!agrees) logMismatchOnce(backendAuthEnabled, frontendAuthEnabled);
  return { backendAuthEnabled, frontendAuthEnabled, agrees };
}

function staticRefusal(language: string): RefusalData {
  const help = getFallbackHelplineSet(language);
  return {
    detail: getStrings(language).authUnavailable,
    escalation_footer: help.escalationFooter,
    crisis_response: null,
    helplines: help.helplines,
  };
}

/**
 * The 503 a sign-in route returns when it detects a mode mismatch.
 *
 * Asks the backend for its localised refusal (built by
 * `openlaw.api.refusal`, so footer and helplines come from the one shared
 * helper). If the backend cannot answer either, falls back to the bundled
 * helpline set -- never a 500 and never a bare status.
 */
export async function authUnavailableResponse(
  language: string,
  options: AgreementOptions = {},
): Promise<Response> {
  const fetchImpl = options.fetchImpl ?? fetch;
  const url = `${apiBase(options)}/auth/unavailable?language=${encodeURIComponent(language)}`;
  try {
    const upstream = await fetchImpl(url, {
      cache: "no-store",
      signal: AbortSignal.timeout(STATUS_TIMEOUT_MS),
    });
    const body: unknown = await upstream.json();
    const refusal = body as Partial<RefusalData>;
    if (refusal.detail && refusal.escalation_footer && refusal.helplines) {
      return Response.json(refusal, { status: 503 });
    }
  } catch {
    // fall through to the bundled copy
  }
  return Response.json(staticRefusal(language), { status: 503 });
}
