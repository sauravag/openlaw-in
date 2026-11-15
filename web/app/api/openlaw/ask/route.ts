import { proxyToBackend } from "@/lib/backend";

// Never cache: an auth-bearing proxy response served to someone else leaks.
export const dynamic = "force-dynamic";

export function POST(request: Request): Promise<Response> {
  return proxyToBackend(request, "/ask");
}
