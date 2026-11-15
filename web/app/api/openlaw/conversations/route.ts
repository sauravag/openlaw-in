import { notFound } from "@/lib/auth/routeHelpers";
import { AUTH_ENABLED } from "@/lib/authMode";
import { proxyToBackend } from "@/lib/backend";

// Never cache: the response is one signed-in person's own list.
export const dynamic = "force-dynamic";

/**
 * The signed-in owner's conversations (final review H1b): what lets a new
 * device, or a re-sign-in, find a "kept" conversation again. GET only. Mode A
 * has no accounts, so the route is a bare 404 there and calls nothing; in
 * Mode B an anonymous caller gets the backend's help-carrying 401.
 */
export async function GET(request: Request): Promise<Response> {
  if (!AUTH_ENABLED) return notFound();
  return proxyToBackend(request, "/conversations");
}
