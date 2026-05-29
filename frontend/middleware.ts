/**
 * Middleware Next.js : protège /admin/* avec un cookie de session signé HMAC.
 *
 * Si la variable d'env `ADMIN_PASSWORD` n'est pas définie, le middleware
 * laisse passer (mode dev). Sinon il vérifie le cookie `admin_session`
 * et redirige vers /admin/login si invalide.
 *
 * La page /admin/login et la route /api/auth/login sont libres d'accès.
 */

import { NextRequest, NextResponse } from "next/server";

export const config = {
  matcher: ["/admin/:path*"],
};

async function expectedToken(password: string): Promise<string> {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    enc.encode(password),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const sig = await crypto.subtle.sign("HMAC", key, enc.encode("admin_session"));
  return btoa(String.fromCharCode(...new Uint8Array(sig)));
}

export async function middleware(req: NextRequest) {
  const password = process.env.ADMIN_PASSWORD;

  // Pas de mot de passe configuré → mode dev, accès libre
  if (!password) {
    return NextResponse.next();
  }

  // La page de login est libre
  if (req.nextUrl.pathname === "/admin/login") {
    return NextResponse.next();
  }

  const cookie = req.cookies.get("admin_session");
  if (!cookie) {
    return redirectToLogin(req);
  }

  let expected: string;
  try {
    expected = await expectedToken(password);
  } catch {
    return redirectToLogin(req);
  }

  if (cookie.value !== expected) {
    return redirectToLogin(req);
  }

  return NextResponse.next();
}

function redirectToLogin(req: NextRequest): NextResponse {
  const url = req.nextUrl.clone();
  const from = url.pathname + url.search;
  url.pathname = "/admin/login";
  url.search = from && from !== "/admin" ? `?next=${encodeURIComponent(from)}` : "";
  return NextResponse.redirect(url);
}
