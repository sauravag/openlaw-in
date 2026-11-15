import { proxyToBackend } from "@/lib/backend";

export const dynamic = "force-dynamic";

export function POST(request: Request): Promise<Response> {
  return proxyToBackend(request, "/documents");
}
