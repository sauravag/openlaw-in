import { revokeSession } from "@/lib/auth/gotrue";
import { readGotrueConfig } from "@/lib/auth/gotrueConfig";
import { notFound, redirectTo } from "@/lib/auth/routeHelpers";
import { clearAnonymousCookie, clearSession, readSession } from "@/lib/auth/session";
import { AUTH_ENABLED } from "@/lib/authMode";
import { isCrossSiteWrite } from "@/lib/csrf";

export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<Response> {
  if (!AUTH_ENABLED) return notFound();
  // Not behind proxyToBackend: without this a cross-site page could force a
  // sign-out.
  if (isCrossSiteWrite(request)) {
    return Response.json({ detail: "Forbidden." }, { status: 403 });
  }
  const session = await readSession();
  const config = readGotrueConfig();
  if (session && config) {
    try {
      await revokeSession(config, session.accessToken);
    } catch (error) {
      console.error("[Auth] revokeSession threw", error instanceof Error ? error.name : "unknown");
    }
  }
  // Unconditional: a GoTrue outage must not leave someone signed in on a
  // shared or cybercafe device, nor hand the next person this browser's
  // anonymous identity (final review M3).
  await clearSession();
  await clearAnonymousCookie();
  return redirectTo("/");
}
