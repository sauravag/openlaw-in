import { checkAuthAgreement } from "@/lib/authAgreement";

// Server-side only: the handler asks the backend directly, so no rewrite (and
// therefore no widening of `next.config.ts`'s enumerated proxy surface) is
// needed. When Task 6 replaces the rewrites with Route Handlers this file is
// already in the target shape.
export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  const agreement = await checkAuthAgreement();
  return Response.json(agreement, { headers: { "Cache-Control": "no-store" } });
}
