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
    return [
      {
        source: "/api/openlaw/:path*",
        destination: `${OPENLAW_API_URL}/:path*`,
      },
    ];
  },
};

export default nextConfig;
