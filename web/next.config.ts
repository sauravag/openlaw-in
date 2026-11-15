import type { NextConfig } from "next";

// The browser never calls the FastAPI backend directly: it talks to this
// origin's enumerated Route Handlers under `app/api/openlaw/`, which forward
// server-side through `lib/backend.ts`. That is what lets the anonymous-quota
// cookie (`openlaw_anon_id`, HttpOnly) survive with zero CORS configuration on
// the API, and what lets `Authorization: Bearer` be attached without the
// token ever being script-readable (a rewrite cannot add a request header).
// Requirement 6 -- the anonymous path must work with no account -- depends on
// the cookie sticking. Routes stay enumerated, never a catch-all, so
// `/readyz` (Postgres/Qdrant reachability) remains unreachable (Task 6 review, M7).
// `NEXT_PUBLIC_OPENLAW_AUTH_ENABLED` must be a compile-time LITERAL in every
// bundle. Next only inlines a variable that is defined at build time; when the
// flag is unset the lookup stays a runtime `process.env` read, the minifier
// cannot fold `if (!AUTH_ENABLED) return null`, and Mode A would ship sign-in
// markup. Defaulting to "false" here makes the build always define it
// (`lib/authMode.ts`, `scripts/check-mode-a-bundle.mjs`).
export function buildEnv(
  source: Record<string, string | undefined> = process.env,
): Record<string, string> {
  return { NEXT_PUBLIC_OPENLAW_AUTH_ENABLED: source.NEXT_PUBLIC_OPENLAW_AUTH_ENABLED ?? "false" };
}

const nextConfig: NextConfig = { env: buildEnv() };

export default nextConfig;
