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
const nextConfig: NextConfig = {};

export default nextConfig;
