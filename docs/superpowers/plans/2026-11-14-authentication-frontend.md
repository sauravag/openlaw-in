# OpenLaw.in Authentication Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two coherent builds of the same product. **Mode A** (the default, no flag) is the open-source build: no login, no signup, no quota, no expiry, no sign-in affordance anywhere — a developer clones the repo, runs `docker compose up`, and has the whole product with zero Supabase setup. **Mode B** (`auth_enabled=true`) is what a public deployment runs: email magic-link sign-in through Supabase, quota, the anonymous-versus-authenticated distinction, anonymous→account migration, sign-out and account deletion.

**The defect this plan closes.** `src/openlaw/api/auth.py` is complete and hard-reviewed — it verifies a Supabase HS256 JWT, pins the algorithm, requires `exp`/`sub`/`aud`/`role`, binds identity to the token subject and never to the email, and never leaks `AuthError.detail`. Quota (`openlaw.db.quota`), conversation ownership (`openlaw.db.conversations`) and document retention (`openlaw.documents.store`) are all built on it. But **nobody can sign in**: there is no login route, no login UI, the client never sends an `Authorization` header, `signup_url` points at a `/signup` that does not exist, and no Supabase project is configured. Every path through the app today is the anonymous one — and `DocumentUpload.tsx` already promises *"This document will be deleted after 24 hours. Sign in to keep it."*, a promise with no mechanism behind it. Mode A makes that sentence unnecessary; Mode B makes it true.

**Architecture:** Four changes, no new runtime dependency on either side.

1. **One flag, two surfaces.** `Settings.auth_enabled` (server, runtime) and `NEXT_PUBLIC_OPENLAW_AUTH_ENABLED` (frontend, inlined at build time by Next). The build-time half is what keeps the sign-in code out of the Mode A bundle. The two must agree, and disagreement is *detected* (Task 3), not left to confuse someone.
2. **`next.config.ts`'s `rewrites()` are replaced by enumerated Route Handlers.** A Next rewrite proxies a request but **cannot inject a request header** — `rewrites` entries accept only `source`/`destination`/`basePath`/`locale`/`has`/`missing` (`web/node_modules/next/dist/docs/01-app/03-api-reference/05-config/01-next-config-js/rewrites.md`); header *responses* are a separate `headers()` key and apply to responses, not upstream requests. So `Authorization: Bearer` can only be attached server-side, in code. This is the plan's central architectural change.
3. **The JWT lives in an HttpOnly cookie and is attached server-side.** The PKCE code exchange happens inside a Route Handler, which talks to Supabase's GoTrue REST API with plain `fetch`. `@supabase/supabase-js` is never installed: its default `localStorage` persistence is XSS-readable, which is unacceptable for a product holding FIRs and medical records, and avoiding it also avoids the bundle cost.
4. **Migration goes through `Owner`, not around it.** Reassigning the anon cookie's conversations and documents to a `user_id` is an `UPDATE` of the same `user_id`/`anon_token` pair that `owner_columns` writes, performed in one transaction, idempotent on retry, and audited in `document_access_log`.

**Tech Stack:** Python 3.12+, FastAPI, SQLAlchemy 2 + Alembic, Postgres 17, PyJWT (already present), Next.js 16.3.6 App Router, React 19, pytest, vitest. **No new dependency, either side.**

**Spec:** `docs/superpowers/specs/2026-09-08-openlaw-in-design.md` (§9 application architecture, §10 safety and trust, §12 self-hosting).

**Depends on:** Plans 1–6. In particular `openlaw.api.auth` (Plan 5 Task 3), `openlaw.db.conversations.Owner`/`owner_clause` and `openlaw.documents.store` (Plan 6 Tasks 2–6), `openlaw.api.refusal` (Plan 5 Task 4), and the `web/` surface (Plan 5 Task 6, Plan 6 Task 15).

---

## Global Constraints

- **Mode A is the default and has no limits.** No `auth_enabled` in the environment means: no sign-in UI, no sign-in route, no quota, no `expires_at` on anything, no retention notice, no `signup_url` in any refusal body, and no sign-in string rendered anywhere. There is no greyed-out button and no dead link. **A developer must be able to use every feature — conversations, uploads, indefinite retention — without creating a Supabase project.**
- **Mode A on a publicly reachable deployment is unbounded spend on the operator's API key.** Mode A is for local and trusted-network use. Every document this plan touches (`README.md`, `.env.example`, `docker-compose.yml`, the Mode A startup log line) says so plainly and above the fold, not in a footnote. There is deliberately **no** spend cap available in Mode A: `auth_enabled=false` forces `quota_enabled=false`, and asking for quota without auth is a startup error (Task 2). That is a narrowing, not an oversight — the project owner's rule is that the no-auth version is never run publicly, so a cap with no sign-in to offer is a state nobody occupies and everybody would have to reason about.
- **The flag is one setting with two expressions, never two switches.** `OPENLAW_AUTH_ENABLED` (server) and `NEXT_PUBLIC_OPENLAW_AUTH_ENABLED` (frontend build). The rule is one-directional: `auth_enabled=false` **forces** `quota_enabled=false` (and every anonymous TTL off), and asking for quota anyway is a startup error; `auth_enabled=true` leaves quota configurable, defaulting on, because logins without caps is a real internal deployment.
- **The JWT must never be readable by JavaScript.** It is stored only in an HttpOnly, `SameSite=Lax`, `Secure`-per-`cookie_secure` cookie and attached to the backend by a Route Handler. No access token, refresh token, or Supabase client is ever handed to the browser. `/api/auth/session` returns `{ signedIn, email }` and nothing else — never a token, never a `sub`.
- **The anonymous path keeps working, unchanged, in both modes.** Someone asking about domestic violence must not have to attach an email. In Mode B, signing in is an upgrade (bigger quota, persistence), never a gate. `openlaw_anon_id` remains the ownership key for anyone not signed in, in **both** modes — `owner_clause` and the `CHECK` constraint forbidding an ownerless row are untouched.
- **`_ensure_anonymous_token` keeps working through the Route Handlers.** The middleware mints the cookie and `Set-Cookie`s it on the response, including on a refusal. A rewrite forwarded `Cookie` and `Set-Cookie` for free; a Route Handler must forward both **explicitly** (Task 6). If it does not, every request looks like a first visit and anonymous accounting silently collapses — which is the same class of defect `app.py`'s docstring documents at the 500 boundary.
- **The backend route surface stays enumerated, never wildcarded.** One Route Handler file per backend route, exactly as `next.config.ts` enumerated one `rewrites` entry per route. A `/:path*` catch-all would expose `/readyz` — which reports Postgres and Qdrant reachability — to any anonymous caller (Task 6 review, M7). No `app/api/openlaw/[...path]/route.ts` is created in this plan, and a test asserts it does not exist.
- **Every new terminal state delivers help.** A failed sign-in, an expired or already-used link, a rate-limited OTP request, an auth-unavailable mismatch, a failed migration, a failed account deletion: each returns a body built by `openlaw.api.refusal.refusal_body`, carrying `escalation_footer`, `crisis_response` when warranted, and structured `helplines`. No bare 400, no bare 401, no untranslated string.
- **`AuthError.detail` never reaches a client, and no token, secret or full email is ever logged.** New code inherits that rule verbatim. A Supabase API key value is never printed, in any log, error, or test output.
- **Localisation: `en` and `hi` only, both dictionaries, no exceptions.** Every new string, every `aria-label`, every placeholder and every error goes into both `EN` and `HI` in `web/lib/strings.ts` (the `UIStrings` interface is the enforcement) and, server-side, into a static EN/HI pair in `refusal.py`. Never a live translation call on an auth error path.
- **Every existing UI safety property is preserved.** Crisis content stays first in DOM order. The static helpline block stays in server-rendered HTML with JavaScript disabled. No unverified prose. Every source a working government link. The sign-in form is a real `<form method="post">` that works with JavaScript off, for the same reason `DocumentUpload` is.
- **No new dependency, either side.** No `@supabase/supabase-js`, no auth library, no crypto package. Supabase is reached by `fetch` against its REST endpoints; PKCE verifier generation uses `crypto.getRandomValues`/`crypto.subtle` from the Web Crypto API that Next's server runtime already provides.
- **Python style:** type hints everywhere, `pathlib.Path`, f-strings, functions under 50 lines, files under 800 lines. **TDD mandatory**, 80% minimum coverage. API tests use `TestClient` with stubbed pipelines — no live Supabase call in any test, ever; GoTrue is stubbed at the `fetch` boundary.
- **Operational:** tests run against `openlaw_test` only, never `openlaw`. No Qdrant writes; `acts_india` is never touched. Alembic migration is additive and reversible.

---

## File Structure

```
src/openlaw/
├── config.py                    # + auth_enabled, supabase_* coherence, quota/TTL composition
├── accounts/                    # NEW package
│   ├── __init__.py
│   ├── migrate.py               # anon-cookie material -> user_id, idempotent, audited
│   └── delete.py                # account + data erasure (DPDP)
├── api/
│   ├── auth_routes.py           # NEW: /auth/status, /account/migrate, DELETE /account
│   ├── app.py                   # + auth_routes router, registered only when enabled
│   ├── deps.py                  # + startup coherence refusal
│   ├── refusal.py               # + auth_* refusal keys (EN/HI)
│   └── routes.py                # signup_url omitted when there is no signup
└── db/
    └── conversations.py         # + reassign_owner (uses owner_clause/owner_columns)

web/
├── next.config.ts               # rewrites() REMOVED
├── proxy.ts                     # NOT created -- see Task 6 note
├── app/
│   ├── api/openlaw/ask/route.ts                        # NEW (replaces rewrite)
│   ├── api/openlaw/documents/route.ts                  # NEW
│   ├── api/openlaw/documents/[documentId]/route.ts     # NEW
│   ├── api/openlaw/conversations/[conversationId]/route.ts  # NEW
│   ├── api/auth/sign-in/route.ts                       # NEW (Mode B only)
│   ├── api/auth/callback/route.ts                       # NEW (Mode B only)
│   ├── api/auth/sign-out/route.ts                       # NEW (Mode B only)
│   ├── api/auth/session/route.ts                        # NEW (Mode B only)
│   ├── api/auth/delete-account/route.ts                 # NEW (Mode B only)
│   ├── signup/page.tsx                                  # NEW (Mode B only)
│   └── page.tsx                                          # auth-aware, mode-aware
├── lib/
│   ├── authMode.ts              # NEW: the single build-time flag read
│   ├── auth/gotrue.ts           # NEW: Supabase REST calls, server-only
│   ├── auth/session.ts          # NEW: HttpOnly session cookie read/write
│   ├── auth/pkce.ts             # NEW: verifier/challenge, Web Crypto
│   ├── backend.ts               # NEW: shared proxy helper for the 4 handlers
│   ├── strings.ts               # + every new string, EN and HI
│   └── documents.ts             # SIGN_IN_PATH becomes mode-aware
└── components/
    ├── SignInForm.tsx           # NEW (Mode B only)
    └── AccountBar.tsx           # NEW (Mode B only)

tests/accounts/, tests/api/test_auth_routes.py, tests/api/test_modes.py,
tests/redteam/test_migration_theft.py,
web/lib/__tests__/auth*.test.ts, web/app/api/__tests__/*.test.ts,
web/components/__tests__/SignInForm.test.tsx, AccountBar.test.tsx
```

---

## Task 1: The flag, and what Mode A means

**Traceability:** Mode A is the default and has no limits; requirement 3 (the app must run with no Supabase configured).
**Changes a safety invariant:** YES — it changes what `quota_enabled` and the anonymous TTLs mean. Read this task's reasoning before changing a default.

**Files:**
- Modify: `src/openlaw/config.py`, `.env.example`, `docker-compose.yml`, `web/.env.example`
- Create: `web/lib/authMode.ts`, `tests/test_auth_mode_config.py`, `web/lib/__tests__/authMode.test.ts`

**Interfaces.** On `Settings`, verbatim:

```python
    # Authentication mode (Plan 7 Task 1). FALSE is the default and the
    # open-source experience: no login, no signup, no quota, no expiry, every
    # feature available with zero Supabase setup. TRUE is what a public
    # deployment runs.
    #
    # Mode A is NOT safe on a publicly reachable deployment: anyone who finds
    # the URL gets unlimited model calls on the operator's API key, with NO
    # spend cap of any kind available -- quota requires auth (see
    # `_compose_auth_mode`), deliberately, because a cap with no sign-in has
    # no next step to offer the person it refuses. That is unbounded spend,
    # not a theoretical risk. Mode A is for local and trusted-network use; a
    # public deployment wants auth_enabled=true.
    #
    # Must agree with the frontend's build-time NEXT_PUBLIC_OPENLAW_AUTH_ENABLED
    # -- see `openlaw.api.auth_routes.auth_status` and Task 3 for how a
    # disagreement is detected rather than left to confuse someone.
    auth_enabled: bool = False
```

In `web/lib/authMode.ts`, verbatim:

```typescript
/**
 * The one place the build-time authentication flag is read.
 *
 * `NEXT_PUBLIC_*` is inlined by Next at build time, so with the flag off a
 * bundler can eliminate every sign-in branch guarded by this constant --
 * which is the point. Mode A must not ship a dead sign-in affordance, a
 * greyed-out button, or anything hinting at an account system.
 *
 * Must agree with the backend's `OPENLAW_AUTH_ENABLED`. See
 * `app/api/openlaw/auth-status` (Task 3) for how a mismatch is surfaced.
 */
export const AUTH_ENABLED: boolean =
  process.env.NEXT_PUBLIC_OPENLAW_AUTH_ENABLED === "true";
```

- [ ] **Step 1: Write `tests/test_auth_mode_config.py` (RED).** Assert `Settings().auth_enabled is False` with a clean environment; assert `OPENLAW_AUTH_ENABLED=true` sets it; extend the existing `test_env_example_agrees_with_compose_self_host_defaults` pattern in `tests/test_config.py` to cover `OPENLAW_AUTH_ENABLED` so `.env.example` and `docker-compose.yml` cannot drift (the same lockstep that Task 7 review C2 established for `OPENLAW_QUOTA_ENABLED`).
- [ ] **Step 2: Write `web/lib/__tests__/authMode.test.ts` (RED).** Assert `AUTH_ENABLED` is false for an unset value, false for `"false"`, false for `"1"`, and true only for the exact string `"true"` — a flag that is truthy for any non-empty string is a flag that turns itself on by typo.
- [ ] **Step 3: Add the setting and the constant (GREEN).**
- [ ] **Step 4: `.env.example` and `docker-compose.yml`.** `OPENLAW_AUTH_ENABLED=false` with the unbounded-spend warning as a comment directly above it, in the same voice as `OPENLAW_QUOTA_ENABLED`'s existing comment. `web/.env.example` gains `NEXT_PUBLIC_OPENLAW_AUTH_ENABLED=false` with a comment stating it is read at **build** time, so changing it requires a rebuild, and that it must match the backend's value.

**Done when:** a clean checkout with no environment file at all constructs `Settings()` with `auth_enabled is False`, and `.env.example`/`docker-compose.yml`/`web/.env.example` all ship `false` with the warning adjacent.

---

## Task 2: Composition — no auth means no limits, and no way to ask for any

**Traceability:** Mode A has no limits of any kind; "compose with `quota_enabled`, do not duplicate it"; the user's rule — *"i will never run the no auth version publicly"*.
**Changes a safety invariant:** YES — `quota_enabled`'s code default changes, the `auth_enabled=false` + `quota_enabled=true` combination becomes a startup error, and anonymous TTLs become conditional.

**Files:**
- Modify: `src/openlaw/config.py`, `src/openlaw/db/conversations.py`, `src/openlaw/documents/store.py`, `src/openlaw/db/retention.py`
- Create: `tests/test_mode_composition.py`

**Reasoning, to be written into the code as a comment.** Quota exists to bound spend on a **public** deployment, and a public deployment runs Mode B. Mode A is local and trusted-network use, full stop — the project owner's stated rule is that the no-auth version is never run publicly. So `auth_enabled=false` + `quota_enabled=true` is not a safety valve; it is a state nobody occupies and everybody has to reason about: a cap with no account to upgrade to, no signup page to point at, and a refusal that has to invent a next step for a person who has none. The combination is therefore made **unrepresentable** rather than discouraged, following this codebase's own precedent — `DocumentCitation` has no `source_url` field at all rather than an optional one, and `expires_at IS NOT NULL` lives inside the purge query rather than behind a `max_age_days` knob that could widen it.

This also settles the defaults inconsistency. `quota_enabled`'s *code* default is `True` today (`config.py:120`) while `.env.example` and `docker-compose.yml` ship `false` — two against one, and a bare `Settings()` returns `True`, so a self-hoster running `uvicorn` directly or constructing `Settings()` in a script gets the hosted tier's limits with no signup to escape them. Under the new rule the code default **follows from `auth_enabled`'s default**, so all three agree.

**Interfaces.** In `config.py`, a `model_validator(mode="after")` named `_compose_auth_mode`:

- `quota_enabled`'s declared default becomes `False`, matching `.env.example`, `docker-compose.yml`, and what `auth_enabled=False` now forces anyway.
- When `auth_enabled` is `False`, `quota_enabled` is **forced** to `False`. If `OPENLAW_QUOTA_ENABLED=true` was explicitly set, the validator **raises** rather than silently ignoring it — a quietly discarded setting is how an operator comes to believe they have a spend cap they do not have. The error names both variables and states the rule, verbatim:

```python
        raise ValueError(
            "OPENLAW_QUOTA_ENABLED=true requires OPENLAW_AUTH_ENABLED=true. "
            "Quota bounds spend on a public deployment, and a public "
            "deployment needs accounts: a cap with no sign-in has no next "
            "step to offer the person it refuses. Set OPENLAW_AUTH_ENABLED"
            "=true (see 'Supabase setup'), or leave both false and keep this "
            "deployment on localhost or a trusted network."
        )
```

- When `auth_enabled` is `True`, `quota_enabled` stays **configurable** and defaults to `True`. The rule is one-directional: a law firm running Mode B internally may well want logins without caps, and sets `OPENLAW_QUOTA_ENABLED=false` to get them. `quota_enabled` is never collapsed into an alias for `auth_enabled`.
- New property, verbatim:

```python
    @property
    def retention_enforced(self) -> bool:
        """True when material not owned by an account expires.

        False in Mode A: there is no account to migrate to, so an expiry is a
        deletion with no remedy -- and Mode A promises indefinite retention.
        `openlaw.db.conversations._anonymous_expiry` and
        `openlaw.documents.store._anonymous_expiry` both consult this, so a
        Mode A row is written with `expires_at IS NULL` and
        `scripts/purge_retention.py` has nothing to find.
        """
        return self.auth_enabled
```

- [ ] **Step 1: Write `tests/test_mode_composition.py` (RED).** Five cases: default (auth off, quota off, `retention_enforced` False); `OPENLAW_AUTH_ENABLED=true` alone (quota True, retention enforced); `OPENLAW_AUTH_ENABLED=true` + `OPENLAW_QUOTA_ENABLED=false` (quota off — Mode B without caps is supported); `OPENLAW_QUOTA_ENABLED=true` alone (**raises** `ValidationError`, and the message names both variable names — assert on both strings, so the remedy cannot be edited out of the error); `OPENLAW_QUOTA_ENABLED=false` with auth off (no error — agreeing with the forced value is not a conflict).
- [ ] **Step 2: Write the `_anonymous_expiry` tests (RED).** In `tests/db/` and `tests/documents/`: with `auth_enabled=False`, `_anonymous_expiry` returns `None` for an `anon_token` owner; with `auth_enabled=True` it returns `now + ttl_hours` as it does today. Both functions currently branch only on `owner.user_id`; they gain a settings check.
- [ ] **Step 3: Implement (GREEN).** Both `_anonymous_expiry` functions take the already-available `Settings` (`store_document` and `create_conversation` both have it in scope) and return `None` when `not settings.retention_enforced`. `src/openlaw/db/retention.py`'s purge functions become no-ops under the same condition, with a log line naming why, so a cron job in Mode A cannot delete a developer's work.
- [ ] **Step 4: Fixture and test fallout.** `tests/api/conftest.py` (line ~60) and `tests/api/test_routes.py` (line ~59) both hardcode `"quota_enabled": True`; under the new rule that is now an invalid `Settings()` unless `auth_enabled=True` is set alongside it, so **both fixtures raise at construction until updated** — every quota test in `test_routes.py` fails at setup, not at assertion, which is the loud failure this change wants. `tests/test_config.py::test_env_example_agrees_with_compose_self_host_defaults` keeps passing (both files ship `false`) but gains `OPENLAW_AUTH_ENABLED`. The two `quota_enabled=False` cases (`test_routes.py` ~457, ~636) keep working unchanged in Mode A. Any test asserting `Settings().quota_enabled is True` must flip.
- [ ] **Step 5: Update the README's quota section.** It currently reads "Quota is disabled by default for self-hosters" and describes `OPENLAW_QUOTA_ENABLED` as a knob a self-hoster may set to `true` to opt into the hosted tier's limits — **that is no longer true on its own** and must now say that setting it requires `OPENLAW_AUTH_ENABLED=true`, with the startup error quoted. **Note:** `README.md` is being edited concurrently by another worker at the time this plan was written — rebase before editing, and do not revert their changes.

**Done when:** `OPENLAW_QUOTA_ENABLED=true` with auth off refuses to start with an error naming both variables; the code default, `.env.example` and `docker-compose.yml` all say `false`; and in Mode A uploading a document and starting a conversation both write `expires_at IS NULL`, `check_quota` is never consulted, and `purge_retention.py` deletes nothing.

---

## Task 3: Mode coherence — startup refusal and mismatch detection

**Traceability:** "a frontend built with auth UI talking to a backend with auth disabled is an incoherent state, and the plan should say how that is detected"; requirement 3.

**Files:**
- Create: `src/openlaw/api/auth_routes.py`, `tests/api/test_auth_routes.py`, `web/app/api/openlaw/auth-status/route.ts`, `web/app/api/__tests__/authStatus.test.ts`
- Modify: `src/openlaw/api/deps.py`, `src/openlaw/api/app.py`, `scripts/verify_selfhost.sh`

**Interfaces.**

```python
# src/openlaw/api/auth_routes.py
@router.get("/auth/status", response_model=AuthStatus)
async def auth_status(request: Request) -> AuthStatus: ...

class AuthStatus(BaseModel):
    """What the frontend needs to know about the server's auth mode.

    Carries NO secret and NO identity: `auth_enabled` and, when enabled, the
    Supabase project URL the browser would be redirected to anyway. The
    `supabase_jwt_secret` is never exposed here or anywhere else.
    """
    auth_enabled: bool
    supabase_url: str | None = None
```

- [ ] **Step 1: Write `tests/api/test_auth_routes.py::test_auth_status_*` (RED).** `/auth/status` returns `{"auth_enabled": false, "supabase_url": null}` in Mode A and never requires a token. In Mode B it returns `true` and the configured URL, and the response body **never** contains the JWT secret (assert the secret string is absent from the serialised body — a positive assertion, not an assumption).
- [ ] **Step 2: Write the startup-coherence test (RED).** `openlaw.api.deps.production_app` (which already refuses to start without a reverse-proxy acknowledgement — see `_require_proxy_acknowledgement`) gains `_require_auth_configuration`: when `auth_enabled` is `True` and either `supabase_url` or `supabase_jwt_secret` is missing or the secret is shorter than `auth._MIN_SECRET_LENGTH`, startup **raises**, naming which variable is missing and never printing a value. Mode B with no Supabase project is not a degraded mode; it is a deployment where nobody can sign in and quota is on, i.e. a product that refuses everyone.
- [ ] **Step 3: Implement both (GREEN).** Register `auth_routes.router` on `create_app` unconditionally — `/auth/status` must answer in Mode A, because answering `false` is how the mismatch is detected. The *other* routes in that module (Tasks 9 and 11) are registered only when `settings.auth_enabled`, so a Mode A deployment has no migration or deletion endpoint at all.
- [ ] **Step 4: The frontend half.** `web/app/api/openlaw/auth-status/route.ts` fetches the backend's `/auth/status`, compares `auth_enabled` with `AUTH_ENABLED` from `lib/authMode.ts`, and returns `{ backendAuthEnabled, frontendAuthEnabled, agrees }`. On disagreement it `console.error`s a single explicit line — *"OPENLAW_AUTH_ENABLED (backend) and NEXT_PUBLIC_OPENLAW_AUTH_ENABLED (frontend build) disagree; rebuild the frontend or change the backend setting"* — and every sign-in route (Task 8) returns the `auth_unavailable` refusal instead of a form that cannot work.
- [ ] **Step 5: `scripts/verify_selfhost.sh`.** Add a check that asserts agreement and prints the remedy. It already verifies the stack end-to-end, so this is where a self-hoster will actually see it.

**Done when:** a Mode A backend behind a Mode B frontend build produces one loud server-side log line and a localised, helpline-carrying refusal at the sign-in route — never a blank page, a 500, or a form that silently does nothing.

---

## Task 4: Fix the `/signup` dead link, in both directions

**Traceability:** Binding requirement 5; "no dead sign-in affordance" in Mode A.

**Files:**
- Modify: `src/openlaw/config.py`, `src/openlaw/api/routes.py`, `web/lib/documents.ts`, `web/components/RefusalNotice.tsx`
- Modify tests: `tests/api/test_routes.py`, `web/components/__tests__/RefusalNotice.test.tsx`

**Reasoning.** Today `signup_url` defaults to `"/signup"`, a page that does not exist, and `routes.py` puts it on every quota and rate-limit refusal; `RefusalNotice.tsx` renders it as an `<a href>`. A refusal that points a person at a 404 is worse than a refusal that does not, because it spends the one action they had left. There are two fixes, not one: make the page exist (Task 10), and stop emitting the link when there is nothing to link to.

- [ ] **Step 1: Write the tests (RED).** In `tests/api/test_routes.py`: Mode A cannot produce a quota refusal at all now (Task 2 forces `quota_enabled=False`), so the assertion is that **no** refusal body built in Mode A — quota, rate limit, or validation — carries `signup_required is True` or a non-null `signup_url`. In Mode B both are present when quota is on. The `None` default is still what makes an unconfigured Mode B deployment omit the link rather than point at a 404. In `RefusalNotice.test.tsx`: the anchor is absent when `signup_url` is null and present when it is set (the component already guards on `data.signup_required && data.signup_url`; the test pins it).
- [ ] **Step 2: Implement (GREEN).** `signup_url`'s default becomes `None`, documented as *"set this to the frontend's real signup page; left unset, refusals omit the link rather than pointing at a 404"*. Both call sites in `routes.py` pass `settings.signup_url if settings.auth_enabled else None`, and `signup_required` follows the same condition — belt and braces, since Task 2 already makes a Mode A quota refusal unreachable.
- [ ] **Step 3: `web/lib/documents.ts`.** `SIGN_IN_PATH` is currently the hardcoded literal `'/signup'` mirroring the old default. Replace it with `AUTH_ENABLED ? "/signup" : null` and type it `string | null`, so a Mode A render cannot produce the link.

**Done when:** grepping the repo for a literal `/signup` finds it only in `web/app/signup/page.tsx`, `lib/documents.ts`'s mode-aware constant, and documentation — and no refusal body in Mode A contains it.

---

## Task 5: Server-side Supabase client, no dependency

**Traceability:** Binding requirement 1 (PKCE/code exchange in a server route, no XSS-readable token store); "no new heavy dependency"; "the method must not be hard-wired".

**Files:**
- Create: `web/lib/auth/gotrue.ts`, `web/lib/auth/pkce.ts`, `web/lib/__tests__/gotrue.test.ts`, `web/lib/__tests__/pkce.test.ts`

**Reasoning.** `@supabase/supabase-js` persists the session in `localStorage` by default, which any XSS reads. Configuring it otherwise is possible but puts the library — and its token-refresh timers — in the browser bundle for a flow that has exactly three HTTP calls. Those three calls are made directly against GoTrue's REST API from the server, which costs no dependency, keeps the token out of the browser by construction rather than by configuration, and leaves the second method (phone OTP) a change of one request body rather than a re-architecture.

**Interfaces, verbatim:**

```typescript
/** The sign-in methods this module can start. Adding "phone" means adding a
 * branch in `requestOtp` and a field to `OtpRequest` -- deliberately not a
 * boolean `isEmail`, so phone OTP for an Indian audience is an addition
 * rather than a rewrite. */
export type AuthMethod = "email";

export interface OtpRequest {
  readonly method: AuthMethod;
  /** The email address for `method: "email"`. */
  readonly identifier: string;
  /** PKCE challenge; the verifier stays server-side in an HttpOnly cookie. */
  readonly codeChallenge: string;
  /** Absolute URL of `app/api/auth/callback`. Must be allow-listed in the
   * Supabase project's Redirect URLs -- see "Supabase setup". */
  readonly redirectTo: string;
}

export type OtpOutcome =
  | { kind: "sent" }
  | { kind: "rate_limited"; retryAfterSeconds: number | null }
  | { kind: "failed"; detail: string };

export type ExchangeOutcome =
  | { kind: "session"; accessToken: string; refreshToken: string; expiresAt: number; email: string }
  | { kind: "expired" }
  | { kind: "failed"; detail: string };

/** POST {supabaseUrl}/auth/v1/otp */
export async function requestOtp(config: GotrueConfig, request: OtpRequest): Promise<OtpOutcome>;
/** POST {supabaseUrl}/auth/v1/token?grant_type=pkce */
export async function exchangeCode(config: GotrueConfig, code: string, verifier: string): Promise<ExchangeOutcome>;
/** POST {supabaseUrl}/auth/v1/logout */
export async function revokeSession(config: GotrueConfig, accessToken: string): Promise<void>;
```

`web/lib/auth/pkce.ts` exports `createVerifier(): string` (43–128 chars, `crypto.getRandomValues`, base64url, no padding) and `challengeFor(verifier: string): Promise<string>` (S256 via `crypto.subtle.digest`, base64url).

- [ ] **Step 1: Write `pkce.test.ts` (RED).** Verifier length and alphabet (RFC 7636 §4.1: unreserved characters only); two verifiers differ; `challengeFor` is deterministic and matches a known-answer S256 vector.
- [ ] **Step 2: Write `gotrue.test.ts` (RED).** `fetch` is stubbed, never called for real. Cover: `requestOtp` success → `{kind:"sent"}`; HTTP 429 → `rate_limited` with `Retry-After` parsed and `null` when absent; 4xx/5xx → `failed` with a detail suitable for a **server** log only; `exchangeCode` success → `session` with all five fields; GoTrue's expired/used-code error → `expired` (a distinct outcome, because an expired link is a different sentence to a person than a broken one); network throw → `failed`. Assert that no outcome object, in any branch, contains the access token's value in a `detail` string, and that the module never calls `console.log`.
- [ ] **Step 3: Implement (GREEN).** `"use server"`-only module (no `"use client"` anywhere in the import graph) with a top-of-file comment stating that importing it from a client component is a bug, since it carries the project's API key.

**Done when:** `vitest run` covers every branch and the only `fetch` targets in the file are `/auth/v1/otp`, `/auth/v1/token`, `/auth/v1/logout`.

---

## Task 6: Replace `rewrites()` with enumerated Route Handlers

**Traceability:** The plan's central architectural change — a Next rewrite cannot inject a request header, so `Authorization: Bearer` requires code in the request path. Preserves Task 6 review M7 (`/readyz` must stay unreachable).
**Changes a safety invariant:** YES — it moves the entire browser→backend path. Nothing else in this plan works until it is right.

**Files:**
- Modify: `web/next.config.ts` (delete `rewrites()`)
- Create: `web/lib/backend.ts`, `web/app/api/openlaw/ask/route.ts`, `web/app/api/openlaw/documents/route.ts`, `web/app/api/openlaw/documents/[documentId]/route.ts`, `web/app/api/openlaw/conversations/[conversationId]/route.ts`
- Create tests: `web/app/api/__tests__/proxy.test.ts`, `web/lib/__tests__/backend.test.ts`

**Why not `proxy.ts` (Next 16's renamed middleware).** `NextResponse.next({ request: { headers } })` *can* inject an upstream request header, so a single `proxy.ts` is technically sufficient. It is not used here for three reasons, and this choice is recorded so a later worker does not "simplify" it back: (1) `proxy.ts` runs for a matcher of **paths**, and reinstating a path pattern is how `/readyz` gets exposed again — the enumerated-route property M7 established is cheaper to keep as four files than as one regex; (2) Next's own `proxy.md` warns it is "meant to be invoked separately of your render code and in optimized cases deployed to your CDN", i.e. it is the wrong place for a session-cookie read that must never be cached; (3) a Route Handler is an ordinary async function and is unit-testable under vitest without a Next server, which is what lets Task 16 test both modes cheaply.

**Interfaces.** `web/lib/backend.ts`, verbatim in intent:

```typescript
/**
 * Proxies one request to the FastAPI backend, server-side.
 *
 * Replaces what `next.config.ts`'s `rewrites()` did, because a rewrite cannot
 * add a request header and `Authorization: Bearer` must be attached here --
 * never in the browser, where the token would have to be JavaScript-readable.
 *
 * Three things a rewrite did for free and this MUST do explicitly:
 *   1. Forward the inbound `Cookie` header, so `openlaw_anon_id` reaches
 *      `openlaw.api.app._ensure_anonymous_token`.
 *   2. Forward every `Set-Cookie` from the backend response, so a first-time
 *      caller's freshly minted anon cookie actually lands in the browser --
 *      including on a refusal, which is precisely the case that middleware
 *      exists to cover.
 *   3. Preserve the status code and `Retry-After`, so a 429's structured
 *      refusal body and retry hint survive the hop.
 * Dropping (1) or (2) makes every request look like a first visit: anonymous
 * ownership and quota silently reset per request. That is wrong, not
 * imprecise -- see `app.py`'s docstring on the same boundary.
 */
export async function proxyToBackend(
  request: Request,
  backendPath: string,
): Promise<Response>;
```

- [ ] **Step 1: Write `backend.test.ts` (RED).** With `fetch` stubbed: the inbound `Cookie` header is forwarded verbatim; a backend `Set-Cookie` appears on the returned response (assert via `response.headers.getSetCookie()`, which handles the multi-value case a plain `get` flattens wrongly); status and `Retry-After` are preserved; a `multipart/form-data` body is streamed through unmodified (the 10 MiB upload must not be buffered into a string); `Authorization` is attached when and only when a session cookie is present **and** `AUTH_ENABLED`; no inbound client-supplied `Authorization` header is ever forwarded (a browser must not be able to hand the backend a token of its choosing through this proxy — the backend treats an unverifiable token as anonymous, but forwarding one blurs who chose it).
- [ ] **Step 2: Write `proxy.test.ts` (RED).** Each of the four handlers forwards to the right backend path and exports only the methods its route needs (`POST` for `/ask` and `/documents`, `DELETE` for `/documents/[documentId]`, `GET`+`DELETE` for `/conversations/[conversationId]` — matching `openlaw.api.artifacts`). Assert that **no** handler exists for any other backend path: a test walks `web/app/api/openlaw/` and fails on an unexpected directory, and fails specifically on a `[...path]` catch-all, naming `/readyz` in the failure message.
- [ ] **Step 3: Implement (GREEN).** Four thin files, each a `proxyToBackend` call. `export const dynamic = "force-dynamic"` on each: a cached auth-bearing proxy response would serve one person's answer to another.
- [ ] **Step 4: Delete `rewrites()` from `next.config.ts`.** Keep the file's existing comment about why the proxy exists at all (same-origin cookie survival with zero CORS), rewritten to point at `lib/backend.ts`, so the reason survives the mechanism.
- [ ] **Step 5: Verify the anonymous path end to end in Mode A.** With `auth_enabled=false` and no session cookie: ask a question, upload a document, read the conversation back, delete it. The `openlaw_anon_id` cookie must be set once on the first response and sent on every subsequent one.

**Done when:** `web/lib/api.ts` is unchanged (its endpoint constants still resolve), the anonymous path behaves identically to before, and no catch-all route exists.

---

## Task 7: The HttpOnly session cookie

**Traceability:** Binding requirement 1 — the JWT must never be readable by JavaScript.
**Changes a safety invariant:** YES — this is the new trust boundary on the frontend side.

**Files:**
- Create: `web/lib/auth/session.ts`, `web/lib/__tests__/session.test.ts`

**Interfaces, verbatim:**

```typescript
/** Cookie holding the Supabase session. HttpOnly, SameSite=Lax, Path=/,
 * Secure unless OPENLAW_COOKIE_SECURE=false (plain-HTTP local development --
 * the same reason `Settings.cookie_secure` cannot be unconditional). */
export const SESSION_COOKIE_NAME = "openlaw_session";
/** Short-lived cookie holding the PKCE verifier between the sign-in request
 * and the callback. HttpOnly, SameSite=Lax, 10 minutes, deleted on exchange. */
export const PKCE_COOKIE_NAME = "openlaw_pkce";
const PKCE_COOKIE_MAX_AGE_SECONDS = 600;

export interface StoredSession {
  readonly accessToken: string;
  readonly refreshToken: string;
  /** Unix seconds. */
  readonly expiresAt: number;
  readonly email: string;
}

export async function readSession(): Promise<StoredSession | null>;
export async function writeSession(session: StoredSession): Promise<void>;
export async function clearSession(): Promise<void>;
export async function readVerifier(): Promise<string | null>;
export async function writeVerifier(verifier: string): Promise<void>;
export async function clearVerifier(): Promise<void>;
```

- [ ] **Step 1: Write `session.test.ts` (RED).** Every cookie this module sets is `httpOnly: true`, `sameSite: "lax"`, `path: "/"`, and `secure` per the environment — asserted field by field, because a missing `httpOnly` here is the whole vulnerability this design exists to avoid. A round-trip preserves all four `StoredSession` fields. `readSession` returns `null` for a missing cookie, for malformed JSON, and for `expiresAt` in the past (an expired session is not a session; the person is shown the sign-in form again, not an error). `clearSession` sets an expired cookie rather than only deleting, so a stale copy cannot survive.
- [ ] **Step 2: Implement (GREEN)** using `cookies()` from `next/headers`. The cookie value is the JSON `StoredSession`. It is **not** encrypted: it is HttpOnly and never leaves the server→browser channel, the access token inside it is already a signed JWT the backend independently verifies, and adding an app-level encryption key here would add a secret to manage without closing a path an attacker who can read the cookie does not already have. A comment states that reasoning so it is a decision rather than an omission.
- [ ] **Step 3: Refresh.** `readSession` returns `null` within 60 seconds of `expiresAt` rather than racing the backend's `exp` check. Token refresh (GoTrue `grant_type=refresh_token`) is explicitly **out of scope** for this plan and listed in "Risks and open questions": a person whose token expires is shown the sign-in form, which is honest and costs one magic link.

**Done when:** no module under `web/lib/auth/` is reachable from a `"use client"` component, and `grep -rn "localStorage\|sessionStorage" web/lib web/components web/app` returns nothing related to auth.

---

## Task 8: Sign-in, callback, sign-out, session routes

**Traceability:** Binding requirements 1, 6, 8; "signing in is an upgrade, never a gate".

**Files:**
- Create: `web/app/api/auth/sign-in/route.ts`, `web/app/api/auth/callback/route.ts`, `web/app/api/auth/sign-out/route.ts`, `web/app/api/auth/session/route.ts`
- Create tests: `web/app/api/__tests__/authRoutes.test.ts`

**Interfaces.**

- `POST /api/auth/sign-in` — accepts `email` and `language` from either JSON **or** `application/x-www-form-urlencoded` (the no-JavaScript form posts the latter). Validates the address shape server-side before any outbound call. Mints a PKCE verifier, stores it in `PKCE_COOKIE_NAME`, calls `requestOtp`, and on `{kind:"sent"}` returns 202 with a localised "check your email" body. On `rate_limited` returns 429 with `Retry-After` and the `auth_rate_limited` refusal; on `failed` returns 503 with `auth_failed`. **Never** reports whether the address is registered — an enumeration oracle on a legal-aid product tells an abuser whether their target has an account here.
- `GET /api/auth/callback?code=...` — reads the verifier, calls `exchangeCode`, and on success writes the session cookie, calls the backend's `POST /account/migrate` with the bearer attached (Task 11), clears the verifier, and redirects to `/`. On `expired` redirects to `/signup?error=expired`; on `failed` to `/signup?error=failed`. A missing verifier is treated as `failed` — a callback with no verifier is either a stale tab or a CSRF attempt, and both are handled the same way.
- `POST /api/auth/sign-out` — calls `revokeSession` best-effort, then `clearSession` **unconditionally**: a GoTrue outage must not trap someone in a signed-in state on a shared or cybercafe device. Redirects to `/`.
- `GET /api/auth/session` — returns `{ signedIn: boolean, email: string | null }`. Nothing else, ever. No token, no `sub`, no expiry.

- [ ] **Step 1: Write `authRoutes.test.ts` (RED).** Mode A: every one of the four routes returns 404 (the module checks `AUTH_ENABLED` first and the route is not registered) — Mode A must have no sign-in surface at all. Mode B: the happy path writes an HttpOnly session cookie and no body field contains either token; a malformed email is rejected before `requestOtp` is called; a 429 from GoTrue produces 429 + `Retry-After` + helplines; an expired code redirects to `/signup?error=expired`; `/api/auth/session` returns exactly two keys; sign-out clears the cookie even when `revokeSession` throws. Assert every error body carries `escalation_footer` and a non-empty `helplines` array.
- [ ] **Step 2: Implement (GREEN).** Each route reads the shared refusal bodies from the backend's `/auth/status`-adjacent helper or, simpler and preferred, proxies its error shape from the backend's `refusal.py` so there is exactly one source of helpline text. State in a comment which of the two was chosen and why.
- [ ] **Step 3: Rate-limit the sign-in route itself.** GoTrue's own limit is per-project, not per-caller; a cheap in-process per-IP counter in front of `requestOtp` keeps one script from burning the project's email quota and locking real users out. Reuse the shape of `app.state.rate_limit_hits` rather than inventing a second mechanism.

**Done when:** a Mode B sign-in produces a magic link, the callback lands signed in, no token is observable from `document.cookie` or any response body, and every failure path renders helplines.

---

## Task 9: Reassigning an owner, in the database

**Traceability:** Binding requirement 4 — migration must be idempotent, audited and safe, and must go through the `Owner` model.
**Changes a safety invariant:** YES — it is the only code that changes who owns a row.

**Files:**
- Create: `src/openlaw/accounts/__init__.py`, `src/openlaw/accounts/migrate.py`, `tests/accounts/__init__.py`, `tests/accounts/test_migrate.py`
- Modify: `src/openlaw/db/conversations.py` (add `reassign_owner`)

**Interfaces, verbatim:**

```python
@dataclass(frozen=True)
class MigrationResult:
    """What one migration attempt moved. All zeros on a repeat attempt."""
    conversations_moved: int
    documents_moved: int
    already_migrated: bool


def migrate_anonymous_material(
    session: Session,
    anon_token: str,
    user_id: str,
    settings: Settings,
) -> MigrationResult:
    """Reassign everything owned by `anon_token` to `user_id`.

    Goes through `owner_clause`/`owner_columns` (`openlaw.db.conversations`),
    never a hand-written WHERE: the anon-owner predicate is
    `user_id IS NULL AND anon_token = :token`, which is exactly what makes
    this incapable of touching a row some other account already owns. The
    CHECK constraint forbidding an ownerless row holds throughout, because
    every UPDATE sets `user_id` and NULLs `anon_token` in the same statement.

    Idempotent. A retry -- a double-submitted callback, a user refreshing the
    redirect -- finds nothing matching the anon predicate (the first attempt
    NULLed `anon_token` on every row it moved) and returns all zeros with
    `already_migrated=True`. There is no "migrated" flag to get out of sync;
    the absence of matching rows IS the idempotency.

    Clears `expires_at` on everything it moves: an authenticated row is
    retained until the person deletes it, which is the promise
    `DocumentUpload`'s retention notice makes.

    One transaction. A partial migration -- conversations moved, documents
    orphaned at their 24-hour expiry -- would delete the paperwork and keep
    the discussion of it.

    Writes one `document_access_log` row per document moved, with
    action="migrate", via `openlaw.documents.store.audit` and the DESTINATION
    owner, so the trail answers "which account claimed this document, and
    when".

    THREAT, stated rather than mitigated away: anyone holding a person's
    `openlaw_anon_id` cookie value can migrate that person's material into
    their own account. What limits it: the cookie is HttpOnly and
    SameSite=Lax, so script and cross-site reads are out; it is only sent to
    this origin; obtaining it means the device, the browser profile, or a TLS
    interception -- and an attacker with any of those can already read the
    material directly, without an account. What migration adds is
    PERSISTENCE (the attacker keeps access after the cookie is cleared) and
    an audit trail (`document_access_log` names the claiming account). It
    does NOT grant access to anything the cookie holder could not already
    read. This is why migration is bound to a cookie the browser never
    exposes, and why it is never keyed by anything a person could type.
    """
```

- [ ] **Step 1: Write `tests/accounts/test_migrate.py` (RED), against `openlaw_test` only.** Cases: moves conversations and documents owned by the token; clears `expires_at` on both; leaves another token's rows untouched; leaves a row already owned by a different `user_id` untouched (construct one directly and assert the count is zero); a second call returns all zeros and `already_migrated=True`; writes exactly one `document_access_log` row per document with `actor_kind="user"` and `actor_ref=user_id`; a forced failure mid-way (patch the document update to raise) leaves **nothing** moved, conversations included.
- [ ] **Step 2: Write the red-team test** `tests/redteam/test_migration_theft.py`: migrating with token B while signed in as user A moves only B's rows into A, and a token that owns nothing produces zeros rather than an error — so a probe learns nothing about whether a token exists.
- [ ] **Step 3: Implement (GREEN).** `reassign_owner(session, model, from_owner, to_owner)` in `conversations.py` is the single `UPDATE ... WHERE owner_clause(...)` helper both row types use; `migrate.py` orchestrates and audits. Functions under 50 lines.

**Done when:** every test above passes, coverage of `accounts/migrate.py` is at or above 90%, and no SQL in the module mentions `anon_token` outside `owner_clause`/`owner_columns`.

---

## Task 10: The migration endpoint, and wiring it to the callback

**Traceability:** Requirement 4; "anonymous material migrates to the account on sign-in" so `DocumentUpload`'s existing sentence becomes true.

**Files:**
- Modify: `src/openlaw/api/auth_routes.py`, `src/openlaw/api/app.py`, `web/app/api/auth/callback/route.ts`
- Modify tests: `tests/api/test_auth_routes.py`

**Interfaces.**

```python
@router.post("/account/migrate", response_model=MigrationResponse)
async def migrate_account(request: Request) -> MigrationResponse:
    """Claim the caller's anonymous material for their signed-in account.

    Registered only when `settings.auth_enabled`. Requires a verified bearer
    token -- an unverifiable one is a refusal here, NOT a fall back to the
    anonymous allowance the way `/ask` handles it: there is no anonymous
    meaning for "migrate to my account".

    The anonymous token comes from `request.state.anon_token`
    (`_ensure_anonymous_token`), never from the request body. A body-supplied
    token would let anyone migrate any token they could guess or obtain by
    any means at all, which is strictly weaker than requiring the HttpOnly
    cookie the browser alone can send.
    """

class MigrationResponse(BaseModel):
    conversations_moved: int
    documents_moved: int
```

- [ ] **Step 1: Write the tests (RED).** 404 in Mode A. In Mode B: a valid token migrates and returns counts; a missing/invalid/expired token returns a refusal body with helplines and never `AuthError.detail`; a repeat call returns zeros and 200 (not an error — the callback may legitimately retry); the response never reveals whether the anon token owned anything belonging to someone else.
- [ ] **Step 2: Implement (GREEN).** `upsert_user` is called first and on a session holding nothing else pending — its docstring's `_flush_or_deny` warning is explicit that an `IntegrityError` rolls back the **whole** transaction, so the migration must be staged after the user row is committed, not alongside it. Commit the upsert, then run the migration in its own transaction.
- [ ] **Step 3: Wire the callback.** After `writeSession`, the callback route calls `POST /api/openlaw/...` → backend `/account/migrate` through `proxyToBackend` with the bearer attached. A migration failure does **not** fail the sign-in: the person is signed in, the server logs the failure, and the redirect carries `?migration=failed` so the UI can say plainly that their earlier material could not be moved and is still reachable in the same browser until it expires. Silently discarding it is the defect class this project keeps finding.

**Done when:** signing in with an anonymous conversation and an uploaded document in the same browser results in both being listed under the account with `expires_at IS NULL`, and `document_access_log` has a `migrate` row for the document.

---

## Task 11: Account deletion

**Traceability:** Binding requirement 6 — authenticated documents are retained indefinitely by design, so erasure must exist. India's DPDP Act makes this an obligation, not a courtesy.
**Changes a safety invariant:** YES — it is the only code that destroys user data on request.

**Files:**
- Create: `src/openlaw/accounts/delete.py`, `tests/accounts/test_delete.py`, `web/app/api/auth/delete-account/route.ts`
- Modify: `src/openlaw/api/auth_routes.py`, `src/openlaw/config.py`

**Interfaces, verbatim:**

```python
@dataclass(frozen=True)
class DeletionResult:
    conversations_deleted: int
    documents_deleted: int
    messages_deleted: int
    audit_rows_retained: int


def delete_account(session: Session, user_id: str) -> DeletionResult:
    """Erase `user_id`'s account and everything it owns.

    Order matters: documents (and their excerpt rows) first, then messages,
    then conversations, then the `users` row -- so a failure part-way leaves
    orphaned-but-owned rows rather than ownerless ones, which the CHECK
    constraint forbids outright.

    `document_access_log` rows are deliberately NOT deleted, and the count is
    returned so the caller can say so honestly. They are the audit trail
    establishing who read what, they carry no document content, and
    `DocumentAccessLog.document_id` is deliberately not a foreign key for
    exactly this reason (see `openlaw.documents.store.audit`). Under DPDP the
    legitimate-purpose basis for retaining an access log survives erasure of
    the content; the UI must TELL the person this rather than implying a
    total wipe.
    """
```

```python
@router.delete("/account", response_model=DeletionResponse)
async def delete_own_account(request: Request) -> DeletionResponse: ...
```

- [ ] **Step 1: Write `tests/accounts/test_delete.py` (RED).** Deletes the user's conversations, messages, documents and excerpt rows; leaves another user's rows and every anonymous row untouched; leaves `document_access_log` intact and reports the count; re-running on an already-deleted id returns all zeros rather than raising; a forced mid-way failure leaves no ownerless row (assert the CHECK constraint was never violated and the transaction rolled back whole).
- [ ] **Step 2: Write the endpoint tests (RED).** 404 in Mode A. Mode B: requires a verified bearer; deletes only the token subject's data (never an id from the body or a query parameter — there is no admin path here); returns counts; a second call succeeds with zeros.
- [ ] **Step 3: The Supabase identity.** Deleting the local rows does not delete the Supabase user, and GoTrue has no self-serve delete without a service-role key. Add **optional** `supabase_service_role_key: str | None = None`, documented as *"optional; when set, deleting an account also deletes the Supabase identity. Leave unset and the local data is still erased — delete the identity from the Supabase dashboard. Never expose this value to the frontend; it bypasses every row-level policy."* When unset, the deletion response carries a localised note saying the sign-in identity remains and how to have it removed. Half-done erasure presented as complete is worse than erasure that states its boundary.
- [ ] **Step 4: Frontend route and confirmation.** `POST /api/auth/delete-account` requires a typed confirmation word, localised in both languages (never a bare "Are you sure?" dialog that a mis-tap satisfies), calls the backend, clears the session, and redirects to `/` with a localised confirmation that names what was and was not deleted.

**Done when:** a Mode B user can delete their account from the UI, their rows are gone from `openlaw_test`, the access log survives, and the UI says both of those things in both languages.

---

## Task 12: Refusal copy for every new terminal state

**Traceability:** Binding requirement 8 — every new terminal state delivers help, through `refusal.py`'s existing shared helper.

**Files:**
- Modify: `src/openlaw/api/refusal.py`
- Modify tests: `tests/api/test_refusal.py` (or the module's existing test file)

**Interfaces.** New keys for `refusal_detail`, each with an EN and a HI string in the existing static pair — never a live translation call on an error path:

```
auth_link_sent          auth_link_expired       auth_failed
auth_rate_limited       auth_unavailable        auth_migration_failed
account_deleted         account_delete_failed
```

- [ ] **Step 1: Write the tests (RED).** Every key resolves in both `en` and `hi`; no key's Hindi value is byte-identical to its English (the trap `_looks_non_latin` already guards elsewhere); `refusal_body` for each key carries a non-empty `escalation_footer` and a non-empty `helplines` list; a crisis question that *also* fails sign-in still gets its `crisis_response`. Extend the existing `tests/answers/test_helpline_consistency.py` sweep so the new keys are covered by the same consistency check as every other refusal.
- [ ] **Step 2: Implement (GREEN).** Wording rules: `auth_link_expired` names the remedy ("ask for a new link") and does not blame the person; `auth_rate_limited` gives a concrete wait; `auth_unavailable` says sign-in is not available on this deployment and that **everything else still works** — because in that state it does.
- [ ] **Step 3: Mirror all eight into `web/lib/strings.ts`** (both dictionaries), for the states the frontend renders without a backend round trip.

**Done when:** every new terminal state's body, in both languages, carries the escalation footer and helplines, and the helpline-consistency sweep covers all eight keys.

---

## Task 13: The sign-in UI — Mode B only, out of the bundle in Mode A

**Traceability:** Requirement 7 (every existing UI safety property preserved); Mode A has no sign-in affordance.

**Files:**
- Create: `web/components/SignInForm.tsx`, `web/components/AccountBar.tsx`, `web/app/signup/page.tsx`
- Create tests: `web/components/__tests__/SignInForm.test.tsx`, `AccountBar.test.tsx`, `web/app/__tests__/signup.test.tsx`
- Modify: `web/lib/strings.ts`

- [ ] **Step 1: Write the component tests (RED).** `SignInForm` is a real `<form method="post" action="/api/auth/sign-in">` with a named `email` input, so it works with JavaScript off — the same reason `DocumentUpload` posts to a real endpoint. Every label, placeholder, button and `aria-label` comes from `getStrings(language)`; a test asserts no literal English string appears in the rendered Hindi tree. The email input is `type="email"` with `autoComplete="email"` and `inputMode="email"`, which is what a cheap Android keyboard needs. `AccountBar` shows the email and a sign-out button when signed in, the sign-in link when not, and **renders nothing at all** when `AUTH_ENABLED` is false.
- [ ] **Step 2: `app/signup/page.tsx` (RED then GREEN).** Server-rendered. Reads `?error=` and renders the matching localised refusal copy **with the static helpline block**, because a person who cannot get into the product is a person who may need the helpline more, not less. In Mode A the route is not built at all (the module early-returns `notFound()` behind `AUTH_ENABLED`), so the dead link Task 4 removed cannot come back as a live-but-empty page.
- [ ] **Step 3: Strings.** Add every new key to `UIStrings` and to both dictionaries. `UIStrings` being a typed interface means a key added to `EN` alone will not compile — that is the enforcement, and this task leans on it rather than on review.
- [ ] **Step 4: Bundle check.** A test (or a documented `next build` assertion) confirms that with `NEXT_PUBLIC_OPENLAW_AUTH_ENABLED` unset, no chunk contains the string `"/auth/v1/otp"` or `"openlaw_session"`. This is the concrete, checkable form of "the login code should ideally not even be in the bundle".

**Done when:** a Mode A build contains no sign-in markup and no auth string; a Mode B build renders a working no-JavaScript sign-in form in both languages.

---

## Task 14: `page.tsx` — real session state, honest retention notice

**Traceability:** Requirement 2 (the anonymous path is unchanged for people who never sign in); the `DocumentUpload` promise.
**Changes a safety invariant:** YES — the retention notice is a promise to a person about their FIR.

**Files:**
- Modify: `web/app/page.tsx`, `web/components/DocumentUpload.tsx`, `web/lib/documents.ts`
- Modify tests: `web/app/__tests__/page.test.tsx`, `web/components/__tests__/DocumentUpload.test.tsx`

**Reasoning.** `page.tsx` passes `isSignedIn={false}` as a literal today, and `DocumentUpload`'s prop docstring says so. Three states now exist, not two: Mode A (no expiry, no account — the notice must **not** render, because nothing will be deleted and there is nothing to sign into), Mode B anonymous (the notice renders as written, and is now true), Mode B signed in (a "kept until you delete it" notice).

- [ ] **Step 1: Write the tests (RED).** Mode A: no retention notice, no `signInCta`, no sign-in link anywhere in the tree, and the document list shows no expiry. Mode B anonymous: the existing 24-hour notice with the sign-in call to action. Mode B signed in: the retained notice, no sign-in call to action. In all three, assert the crisis block is still first in DOM order and the static helpline block is still present — the tests for both already exist and must keep passing unchanged.
- [ ] **Step 2: Implement (GREEN).** `page.tsx` fetches `/api/auth/session` once on mount **only when `AUTH_ENABLED`** (in Mode A there is no request to make and no loading state to render), and threads `isSignedIn` into `DocumentUpload` and `AccountBar`. `DocumentUpload` takes a `retention: "none" | "expires" | "kept"` prop rather than booleans — three states deserve three names, and a second boolean here is how the wrong pair gets rendered together.
- [ ] **Step 3: `anonymousUploadNotice` stays, `SIGN_IN_PATH` becomes nullable** (Task 4), and the component no longer constructs a link when it is null.

**Done when:** every existing `page.test.tsx` and `DocumentUpload.test.tsx` assertion passes in Mode B anonymous (the behaviour they were written against), and the Mode A variants assert the absence of every account affordance.

---

## Task 15: Testing both modes

**Traceability:** "a flag that is only exercised in one state is a flag that breaks in the other".

**Files:**
- Create: `tests/api/test_modes.py`, `web/app/api/__tests__/modes.test.ts`
- Modify: `tests/api/conftest.py`, `tests/conftest.py`, `web/vitest.setup.ts`, `pyproject.toml`

- [ ] **Step 1: Parametrise the fixtures.** `tests/api/conftest.py`'s settings fixture gains an `auth_enabled` parameter defaulting to `False` (Mode A — the product's default must be the test default, or the default is the untested path). A `mode_b_settings` fixture provides `auth_enabled=True` with a 64-character dummy JWT secret and a dummy project URL. **No test ever contacts Supabase.** On the frontend, `web/vitest.setup.ts` gains a helper that sets and restores `process.env.NEXT_PUBLIC_OPENLAW_AUTH_ENABLED` per test, since the constant is read at module load — tests must `vi.resetModules()` between modes or they will assert against a stale value, and the helper does that for them.
- [ ] **Step 2: Write `tests/api/test_modes.py`.** A table-driven sweep over the whole route surface asserting, for each mode, which routes exist and which return 404: `/auth/status` in both; `/account/migrate` and `DELETE /account` in Mode B only; `/ask`, `/documents`, `/conversations/{id}` in both. Plus: no quota check runs in Mode A (and `quota_enabled` cannot be on there); `Settings(auth_enabled=False, quota_enabled=True)` raises; `expires_at IS NULL` in Mode A and non-null for an anonymous Mode B upload; no refusal body in Mode A contains `signup_url`.
- [ ] **Step 3: Write `web/app/api/__tests__/modes.test.ts`.** The mirror image: every `/api/auth/*` route 404s in Mode A; `proxyToBackend` attaches no `Authorization` in Mode A even when a session cookie is somehow present (defence in depth against a stale cookie from a Mode B build on the same origin).
- [ ] **Step 4: CI.** The Python suite runs twice, once per mode, via a pytest marker or a parametrised session fixture; vitest runs twice via two `NEXT_PUBLIC_OPENLAW_AUTH_ENABLED` values. Document the two commands in the README's testing section. A single-mode CI run is a half-tested flag.

**Done when:** both suites pass in both modes, coverage stays at or above 80% in each, and removing either mode from CI makes a test fail rather than silently reducing coverage.

---

## Task 16: Documentation — Supabase setup, and the Mode A warning

**Traceability:** Requirement 3; the unbounded-spend warning must be where someone sees it before deploying.

**Files:**
- Modify: `README.md`, `.env.example`, `web/.env.example`, `docs/superpowers/specs/2026-09-08-openlaw-in-design.md`

> **Concurrency note for the implementer:** at the time this plan was written, another worker was editing `README.md` and adding `LICENSE`, `scripts/export_corpus.py` and `scripts/fetch_corpus.py`. Rebase before editing and do not revert their work.

- [ ] **Step 1: The Mode A warning, above the fold.** In the README's quickstart — not a footnote, not an appendix — immediately after the "open http://localhost:8080" step: Mode A is the default, has no limits, and **must not be exposed to the public internet**, because anyone who finds the URL gets unlimited model calls on the operator's API key. There is **one** remedy, not two: set `OPENLAW_AUTH_ENABLED=true` and rebuild the frontend with the matching `NEXT_PUBLIC_` value. Quota is not available in Mode A and asking for it is a startup error (Task 2) — state the reason alongside the rule, in the user's own terms: the no-auth build is never run publicly, so the cap-without-accounts knob was removed deliberately and must not be re-added by a later reader who reads its absence as an omission.
- [ ] **Step 2: The "Supabase setup" section** (see below) — copied into the README verbatim, with the two required environment variables and the exact redirect URL.
- [ ] **Step 3: Reconcile the existing quota section** with Task 2. It currently presents `OPENLAW_QUOTA_ENABLED` as independently configurable; under the new rule quota **requires** auth, asking for it without auth is a startup error, and the code default now follows `auth_enabled`'s so code, `.env.example` and `docker-compose.yml` all agree on `false`. Quote the startup error so a search for it lands here.
- [ ] **Step 4: Spec amendment.** Record the two-mode decision in the design spec so a later plan does not reintroduce a single-mode assumption.

**Done when:** a developer who reads only the quickstart knows that the default build is unlimited and local-only, and an operator who follows "Supabase setup" gets a working Mode B deployment without reading anything else.

---

## Supabase setup (for a self-hoster running Mode B)

Skip this entirely for Mode A — the default build needs no Supabase project and has no sign-in.

1. **Create a Supabase project** at supabase.com (the free tier is sufficient). Note its project URL, e.g. `https://abcdefgh.supabase.co`.
2. **Enable email sign-in.** Authentication → Providers → Email: enable it, and turn **off** "Confirm password"/password sign-in — this deployment uses magic links only, and a password provider left on is a credential this service never wants to exist. Authentication → Email Templates: the default "Magic Link" template works; if you edit it, keep the `{{ .ConfirmationURL }}` token.
3. **Allow-list the redirect URL.** Authentication → URL Configuration → Redirect URLs, add **exactly**:

   ```
   https://your-domain.example/api/auth/callback
   ```

   For local development over plain HTTP, add `http://localhost:8080/api/auth/callback` as well. The path is fixed by `web/app/api/auth/callback/route.ts`. A missing entry here is the single most common failure: the magic link opens and Supabase refuses the redirect, which looks like a broken link rather than a configuration gap.
4. **Set the two environment variables** (backend, in `.env` or your orchestrator's secret store):

   ```
   OPENLAW_AUTH_ENABLED=true
   OPENLAW_SUPABASE_URL=https://abcdefgh.supabase.co
   OPENLAW_SUPABASE_JWT_SECRET=<Project Settings -> API -> JWT Secret>
   ```

   Strictly the two that carry Supabase values are `OPENLAW_SUPABASE_URL` and `OPENLAW_SUPABASE_JWT_SECRET`; `OPENLAW_AUTH_ENABLED=true` is the flag that turns the mode on. The JWT secret must be at least 32 bytes (`openlaw.api.auth._MIN_SECRET_LENGTH`) and is **not** the `anon` API key — the anon key is public by design and is deliberately rejected by `decode_token`'s `aud`/`role` checks. Never commit either value and never print them.
5. **Rebuild the frontend with the matching flag.** `NEXT_PUBLIC_OPENLAW_AUTH_ENABLED=true` is inlined at **build** time; changing it requires `next build` again. A backend with auth on and a frontend built with it off (or the reverse) is detected and logged — see Task 3 — but it is still a broken deployment until you rebuild.
6. **Set `OPENLAW_COOKIE_SECURE=true`** once you are behind HTTPS, which you must be: the session cookie and the anonymous cookie both depend on it.
7. **Optional:** `OPENLAW_SUPABASE_SERVICE_ROLE_KEY` lets account deletion also remove the Supabase identity. Leave it unset and local data is still fully erased; the identity is then deleted from the dashboard. This key bypasses every row-level policy — never expose it to the frontend.

---

## Risks and open questions

1. **Migration via a stolen anonymous cookie.** Stated in full in `migrate_anonymous_material`'s docstring (Task 9). It grants persistence and leaves an audit trail; it grants no read the cookie holder lacked. Accepted, documented, audited. **Open:** should migration be offered as an explicit "claim the documents from this device" confirmation rather than happening automatically on sign-in? Automatic is kinder on a cheap phone; explicit is harder to abuse on a shared device. This plan does it automatically and logs it; revisit if shared-device use proves common.
2. **No token refresh.** A Mode B session ends when the access token expires and the person signs in again (Task 7 Step 3). This is honest but costs a magic link. Adding `grant_type=refresh_token` is a contained follow-up; doing it badly (a refresh race writing two cookies) is worse than not doing it.
3. **`openlaw_anon_id` in Mode A is an ownership key with a one-year cookie and no expiry on what it owns.** Clearing it orphans the data permanently — it becomes unreachable but is never deleted, since Mode A disables the purge. For a local developer that is the right trade; on a long-lived trusted-network deployment it accumulates. **Open:** an operator-run `scripts/purge_retention.py --orphaned` mode.
4. **GoTrue's email rate limit is per project.** Task 8 Step 3's per-IP counter reduces but does not eliminate the risk that a script exhausts the project's email allowance and locks out real users. A production deployment should configure a custom SMTP provider in Supabase.
5. **Mode A on a public URL remains possible, and now has no spend cap at all.** Quota requires auth by construction (Task 2), so the only controls left are the per-caller rate limit and `trusted_proxy_ips`. Nothing in the code can stop an operator exposing Mode A; the plan's answer is a prominent warning, a startup log line, and the recorded reason for the narrowing — the no-auth build is never run publicly. **Open:** should Mode A refuse to bind to a non-loopback interface without an explicit acknowledgement, the way `no_reverse_proxy` already forces a choice? That pattern exists in this codebase and would make the warning enforceable rather than advisory. Deliberately not done here — it would break legitimate trusted-network use — but it is now the strongest available answer, since re-adding the quota knob is ruled out.
6. **The session cookie is unencrypted** (Task 7 Step 2), reasoned about rather than overlooked. If a future change ever puts anything in it beyond the two tokens and an email, revisit.
7. **Hindi copy for eight new refusal keys and roughly a dozen UI strings is best-effort**, carrying the same native-speaker-review caveat as `lib/fallbackHelplines.ts` and the existing `HI` dictionary. `scripts/review_translations.py` exists; run it.
8. **Supabase is a hosted dependency in Mode B.** A GoTrue outage means nobody new can sign in. The anonymous path still works, which is the design's answer, and `auth_unavailable` says so in both languages.

---

## Existing tests that must change, and why

| Test | Change | Why |
|---|---|---|
| `tests/test_config.py::test_env_example_agrees_with_compose_self_host_defaults` | Add `OPENLAW_AUTH_ENABLED` to the lockstep list | Same drift this test was written to catch for `OPENLAW_QUOTA_ENABLED` (Task 7 review, C2) |
| `tests/test_config.py` (defaults) | `quota_enabled` default is now `False` | Task 2: the code default follows `auth_enabled`'s, so code, `.env.example` and `docker-compose.yml` finally agree |
| `tests/api/conftest.py` (~line 60), `tests/api/test_routes.py` (~line 59) | Both hardcode `"quota_enabled": True` and must add `"auth_enabled": True`; until they do, `Settings()` **raises at fixture construction** and every quota test fails at setup | Task 2 forces quota off when auth is off and rejects the contradiction rather than ignoring it |
| `tests/api/test_routes.py::...signup_url...` (around line 957) | Must set `auth_enabled=True` to still see a `signup_url` | Task 4 omits it in Mode A |
| `tests/documents/` (expiry assertions), `tests/db/` (conversation expiry) | Must set `auth_enabled=True` to still see a non-null `expires_at` | Task 2 makes expiry a Mode B behaviour |
| `web/components/__tests__/DocumentUpload.test.tsx` | `isSignedIn` boolean prop becomes `retention: "none" \| "expires" \| "kept"`; tests asserting the 24-hour notice must declare Mode B | Task 14 — three states, three names |
| `web/app/__tests__/page.test.tsx` | Mode-aware; the `signup_url` fixtures at lines ~196 and ~273 now need Mode B | Task 14 |
| `web/lib/__tests__/api.test.ts`, `artifactsApi.test.ts` | Endpoint constants unchanged, but any test asserting rewrite behaviour must now exercise the Route Handlers | Task 6 moves the proxy from config into code |
| `tests/answers/test_helpline_consistency.py` | Extend the sweep to the eight new refusal keys | Task 12 — a new terminal state not in the sweep is a terminal state without helplines |
