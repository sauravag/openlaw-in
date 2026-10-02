# OpenLaw.in — chat interface

The frontend for OpenLaw.in: a single page that asks a question against the
`/ask` endpoint (Task 4/5's FastAPI backend) and renders the answer, sources,
and any crisis/refusal state. See `app/page.tsx` for the full design
rationale.

## Setup

```bash
npm install
cp .env.example .env.local   # then edit OPENLAW_API_URL if needed
npm run dev
```

Requires the backend running separately (`uvicorn openlaw.api.deps:production_app --factory`,
or the dev fixture your task uses) — this app only ever talks to it through
the proxy configured in `next.config.ts`.

## Environment variables

| Variable          | Default                  | Read by                          |
|--------------------|---------------------------|-----------------------------------|
| `OPENLAW_API_URL`  | `http://127.0.0.1:8000`  | `next.config.ts` (server-side only, never sent to the browser) |

## Why requests go through a proxy, not straight to the API

`lib/api.ts` calls `/api/openlaw/ask` — a same-origin path — and
`next.config.ts` rewrites it to the real backend. This is deliberate, not
incidental: the backend's anonymous-quota cookie (`openlaw_anon_id`) is
`HttpOnly` and the backend has no CORS middleware, so a direct cross-origin
`fetch` from the browser would never receive or resend that cookie. Routing
through Next.js's own server makes every request same-origin from the
browser's point of view, which is what makes the cookie work with zero
backend changes.

**This has two operational consequences a contributor deploying this needs
to know:**

1. **The Next.js server becomes the backend's TCP peer for every request.**
   `openlaw.api.routes._check_rate_limit` (the short-burst abuse limiter,
   independent of the quota) keys on the raw TCP peer unless that peer is
   listed in `OPENLAW_TRUSTED_PROXY_IPS`. Without it, every visitor to this
   app shares **one** 30-requests-per-60-seconds bucket on the backend,
   because the backend only ever sees Next.js's own address. Set
   `OPENLAW_TRUSTED_PROXY_IPS` (on the **backend**, see the repo root's
   `.env.example`) to the address Next.js's server actually connects from.
   The anonymous *quota* itself is unaffected — it is keyed by the cookie,
   not the IP — only the burst limiter is at risk here.

   *Unverified in this codebase:* whether Next.js's rewrite proxy forwards
   `X-Forwarded-For` to the backend at all. If it does not, setting
   `OPENLAW_TRUSTED_PROXY_IPS` alone will not fix the above — the deployment
   would need a real reverse proxy in front of both processes instead. Check
   this before relying on the trusted-proxy setting in production.

2. **`Secure` cookies need HTTPS, including for on-device testing.** The
   backend's `Settings.cookie_secure` defaults to `True`, which browsers
   honour over plain HTTP everywhere *except* `localhost`. `npm run dev` on
   the same machine as the backend works fine. Testing on an actual phone
   over your LAN (e.g. opening `http://192.168.x.x:3000` from the device
   this product is built for) does **not** — the cookie is silently dropped,
   every request mints a fresh anonymous token, and the free-question count
   never accumulates, with no visible error. For that testing flow, either
   put a TLS-terminating proxy in front of both processes, or set
   `OPENLAW_COOKIE_SECURE=false` on the backend for the duration of the test
   (never in a real deployment).

## Testing

```bash
npm test              # vitest run — 52+ tests across lib/ and components/
npm run test:coverage # same, with the 80% coverage floor enforced (vitest.config.mts)
npm run lint          # eslint
npx tsc --noEmit       # typecheck
npm run build          # next build — also runs a production TypeScript pass
```

No test in this project calls the live backend — everything mocks `fetch`
or `@/lib/api` directly, so the suite passes with no `OPENLAW_API_URL`
configured and no backend running.
