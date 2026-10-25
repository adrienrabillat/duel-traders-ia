/**
 * Proxy des routes admin vers le backend FastAPI.
 *
 * Le navigateur appelle /api/admin/... (protégé par le cookie de session,
 * voir middleware.ts) et c'est ce serveur qui ajoute `ADMIN_API_TOKEN` :
 * le token ne se retrouve jamais dans le JavaScript envoyé au client.
 */

import { NextRequest, NextResponse } from "next/server";

export const runtime = "edge";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function forward(req: NextRequest, { params }: { params: { path: string[] } }) {
  if (process.env.NEXT_PUBLIC_DEMO_MODE === "true") {
    return NextResponse.json({ detail: "Administration désactivée en mode démo" }, { status: 403 });
  }

  const token = process.env.ADMIN_API_TOKEN;
  const url = `${API}/admin/${params.path.map(encodeURIComponent).join("/")}${req.nextUrl.search}`;
  const res = await fetch(url, {
    method: req.method,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: req.method === "GET" ? undefined : await req.text(),
    cache: "no-store",
  });

  return new NextResponse(res.body, {
    status: res.status,
    headers: { "Content-Type": res.headers.get("Content-Type") ?? "application/json" },
  });
}

export { forward as GET, forward as POST };
