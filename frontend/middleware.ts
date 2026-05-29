/**
 * Middleware Next.js : protège /admin/* par HTTP Basic Auth.
 *
 * Si la variable d'env `ADMIN_PASSWORD` n'est pas définie, le middleware
 * laisse passer (mode dev). Sinon le navigateur affiche une popup native
 * de login. Le compte est sans importance — seul le password compte.
 *
 * Pour un usage en équipe, c'est suffisant. À durcir en login + cookie
 * HttpOnly + rotation périodique si l'expérience devient publique.
 */

import { NextRequest, NextResponse } from "next/server";

export const config = {
  // Seules les pages admin sont protégées
  matcher: ["/admin/:path*"],
};

export function middleware(req: NextRequest) {
  const password = process.env.ADMIN_PASSWORD;

  // Pas de password configuré → mode dev, on laisse passer
  if (!password) {
    return NextResponse.next();
  }

  const auth = req.headers.get("authorization");
  if (!auth || !auth.toLowerCase().startsWith("basic ")) {
    return unauthorized();
  }

  try {
    const decoded = atob(auth.slice(6).trim());
    const idx = decoded.indexOf(":");
    const provided = idx >= 0 ? decoded.slice(idx + 1) : decoded;
    if (provided !== password) {
      return unauthorized();
    }
  } catch {
    return unauthorized();
  }

  return NextResponse.next();
}

function unauthorized(): NextResponse {
  return new NextResponse("Authentification requise", {
    status: 401,
    headers: {
      "WWW-Authenticate": 'Basic realm="Les Anges de l\'IA — Admin"',
    },
  });
}
