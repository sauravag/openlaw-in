import { proxyToBackend } from "@/lib/backend";

export const dynamic = "force-dynamic";

type Context = { params: Promise<{ conversationId: string }> };

async function forward(request: Request, { params }: Context): Promise<Response> {
  const { conversationId } = await params;
  return proxyToBackend(request, `/conversations/${encodeURIComponent(conversationId)}`);
}

export const GET = forward;
export const DELETE = forward;
