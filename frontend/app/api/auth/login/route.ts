/**
 * Route de login admin.
 *
 * POST /api/auth/login avec { password } dans le body.
 * Si le password matche ADMIN_PASSWORD (env), pose un cookie HttpOnly
 * `admin_session` signé HMAC-SHA256. Sinon retourne 401.
 *
 * Le middleware vérifie le cookie sur toutes les routes /admin/*.
 */

import { NextRequest, NextResponse } from "next/server";

export const runtime = "edge";

async function signToken(password: string): Promise<string> {
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

export async function POST(req: NextRequest) {
  let body: { password?: string };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ ok: false, error: "invalid_body" }, { status: 400 });
  }

  const expected = process.env.ADMIN_PASSWORD;
  if (!expected) {
    // En dev sans password, on accepte tout
    return NextResponse.json({ ok: true, dev_mode: true });
  }

  const provided = (body.password ?? "").trim();
  if (provided !== expected) {
    return NextResponse.json({ ok: false, error: "wrong_password" }, { status: 401 });
  }

  const token = await signToken(expected);
  const res = NextResponse.json({ ok: true });
  res.cookies.set("admin_session", token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: 60 * 60 * 24, // 24 heures
  });
  return res;
}
