const RETRY_ATTEMPTS = 6;
const RETRY_DELAY_MS = 5000;

// Surface a frontend/backend auth-mode mismatch in the server log at startup,
// not only when someone happens to request /api/openlaw/auth-status. The
// backend may still be starting (compose brings both up together), so retry
// briefly. Deliberately not awaited: `register` blocks readiness, and a
// slow or absent backend must never delay serving the anonymous product.
export function register(): void {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  void (async () => {
    const { checkAuthAgreement } = await import("./lib/authAgreement");
    for (let attempt = 0; attempt < RETRY_ATTEMPTS; attempt += 1) {
      const { agrees } = await checkAuthAgreement();
      if (agrees !== null) return;
      await new Promise((resolve) => setTimeout(resolve, RETRY_DELAY_MS));
    }
  })();
}
