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
export { isAuthFlagOn, TRUTHY_AUTH_FLAG_VALUES } from "./authFlag";

// Final review H3a. The backend's pydantic bool reads `True`, `1`, `yes`,
// `on`, `t` and `y` (any case) as true; `isAuthFlagOn` reads exactly that set
// (pinned by `tests/test_auth_flag_parity.py`). It is applied at BUILD time:
// `next.config.ts`'s `buildEnv` normalises the operator's value to the literal
// "true" or "false" before Next inlines it, and `vitest.config.mts` admits
// only those two. So this comparison is always against a literal, which the
// minifier folds to a constant. A runtime call here would NOT be folded across
// modules, and Mode A would ship sign-in code (`npm run check:mode-a-bundle`
// caught exactly that when it was tried).
export const AUTH_ENABLED: boolean = process.env.NEXT_PUBLIC_OPENLAW_AUTH_ENABLED === "true";
