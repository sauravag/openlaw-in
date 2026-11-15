import { notFound } from "@/lib/auth/routeHelpers";
import { readSession } from "@/lib/auth/session";
import { AUTH_ENABLED } from "@/lib/authMode";

export const dynamic = "force-dynamic";

/** Exactly `{ signedIn, email }`: no token, no `sub`, no expiry, ever. */
export async function GET(): Promise<Response> {
  if (!AUTH_ENABLED) return notFound();
  const session = await readSession();
  return Response.json(
    { signedIn: session !== null, email: session?.email ?? null },
    { headers: { "Cache-Control": "no-store" } },
  );
}
