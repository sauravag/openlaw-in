import type { NextConfig } from "next";

// The FastAPI backend (Task 4/5's `create_app`). Proxying through Next.js
// (see `rewrites` below) rather than calling it cross-origin from the
// browser is what lets the anonymous-quota cookie (`openlaw_anon_id`, set
// HttpOnly by `create_app`'s middleware) survive across requests with zero
// CORS configuration on the API: the browser only ever talks to its own
// origin, and Next.js's server forwards the request (and the Set-Cookie
// response header) on the backend's behalf. Requirement 6 -- the anonymous
// path must work with no account -- depends on that cookie actually sticking.
const OPENLAW_API_URL = process.env.OPENLAW_API_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  async rewrites() {
    // Narrowed to exactly `/ask` (Task 6 review, M7) -- `lib/api.ts` never
    // calls any other backend route, so a wildcard here only exposed
    // infrastructure it did not need to: `/readyz` (`routes.py`) returns
    // Postgres/Qdrant reachability to any anonymous caller, for one example.
    return [
      {
        source: "/api/openlaw/ask",
        destination: `${OPENLAW_API_URL}/ask`,
      },
    ];
  },
};

export default nextConfig;
