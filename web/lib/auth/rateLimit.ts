import "server-only";

/**
 * In-process per-IP limiter in front of `requestOtp`.
 *
 * GoTrue's own email limit is per project, not per caller, so one script
 * could burn the whole quota and lock real users out. This mirrors the shape
 * of the backend's `app.state.rate_limit_hits` (`openlaw.api.routes`): a map
 * of key -> hit timestamps, pruned on every check, sliding window.
 *
 * Differences from the backend copy, both deliberate: the map is also capped
 * (`maxKeys`) so a flood of distinct keys cannot grow it without limit, and
 * time is passed in so tests need no fake timers. State is per process; with
 * several replicas the effective ceiling is multiplied, which is acceptable
 * for abuse control (the project-wide GoTrue limit is the hard stop).
 */

export const SIGN_IN_WINDOW_SECONDS = 600;
export const SIGN_IN_MAX_ATTEMPTS = 10;
/** Distinct addresses tracked at once; the oldest is evicted past this. */
export const SIGN_IN_MAX_TRACKED_KEYS = 10_000;
/** Bucket for requests with no usable `X-Forwarded-For`. */
export const SHARED_BUCKET_KEY = "shared";
const MAX_KEY_LENGTH = 64;

export interface LimiterOptions {
  readonly windowSeconds: number;
  readonly maxAttempts: number;
  readonly maxKeys: number;
}

export type LimiterResult =
  | { allowed: true }
  | { allowed: false; retryAfterSeconds: number };

export interface SignInLimiter {
  check(key: string, nowMs: number): LimiterResult;
  size(): number;
}

export function createSignInLimiter(options: LimiterOptions): SignInLimiter {
  const hits = new Map<string, number[]>();
  const windowMs = options.windowSeconds * 1000;

  function prune(windowStart: number): void {
    for (const [key, times] of hits) {
      const fresh = times.filter((time) => time >= windowStart);
      if (fresh.length === 0) hits.delete(key);
      else if (fresh.length !== times.length) hits.set(key, fresh);
    }
  }

  return {
    check(key, nowMs) {
      prune(nowMs - windowMs);
      const recent = hits.get(key) ?? [];
      if (recent.length >= options.maxAttempts) {
        const waitMs = recent[0] + windowMs - nowMs;
        return { allowed: false, retryAfterSeconds: Math.max(1, Math.ceil(waitMs / 1000)) };
      }
      if (!hits.has(key) && hits.size >= options.maxKeys) {
        // Map iterates in insertion order: evict the oldest tracked key.
        const oldest = hits.keys().next().value;
        if (oldest !== undefined) hits.delete(oldest);
      }
      hits.set(key, [...recent, nowMs]);
      return { allowed: true };
    },
    size: () => hits.size,
  };
}

/**
 * The limiter key for a request: the FIRST `X-Forwarded-For` value, or one
 * shared bucket when the header is absent.
 *
 * nginx overwrites `X-Forwarded-For` with `$remote_addr` in deployment, so the
 * first value is the real peer. In `next dev` WITHOUT nginx the header is
 * client-controlled, so a caller can pick their own bucket: fine for local
 * development, never a production configuration.
 */
export function clientKey(request: Request): string {
  const first = request.headers.get("x-forwarded-for")?.split(",")[0].trim();
  return first ? first.slice(0, MAX_KEY_LENGTH) : SHARED_BUCKET_KEY;
}

export const signInLimiter: SignInLimiter = createSignInLimiter({
  windowSeconds: SIGN_IN_WINDOW_SECONDS,
  maxAttempts: SIGN_IN_MAX_ATTEMPTS,
  maxKeys: SIGN_IN_MAX_TRACKED_KEYS,
});
