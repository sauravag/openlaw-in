import { proxyToBackend } from "@/lib/backend";

export const dynamic = "force-dynamic";

type Context = { params: Promise<{ documentId: string }> };

export async function DELETE(request: Request, { params }: Context): Promise<Response> {
  const { documentId } = await params;
  return proxyToBackend(request, `/documents/${encodeURIComponent(documentId)}`);
}
