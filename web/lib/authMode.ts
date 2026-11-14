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
