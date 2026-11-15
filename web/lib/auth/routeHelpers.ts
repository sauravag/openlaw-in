import "server-only";

const SEE_OTHER = 303;
const NOT_FOUND = 404;

/** Relative `Location` on purpose: never built from a client-supplied host. */
export function redirectTo(path: string): Response {
  return new Response(null, {
    status: SEE_OTHER,
    headers: { Location: path, "Cache-Control": "no-store" },
  });
}

/** Mode A has no sign-in surface at all. */
export function notFound(): Response {
  return new Response(null, { status: NOT_FOUND });
}
