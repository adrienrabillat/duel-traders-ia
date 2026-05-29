"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

export default function AdminLoginPage() {
  return (
    <Suspense fallback={<div className="card text-muted">Chargement…</div>}>
      <LoginForm />
    </Suspense>
  );
}

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const nextPath = searchParams.get("next") || "/admin";

  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });
      if (res.ok) {
        router.push(nextPath);
        router.refresh();
      } else {
        setError("Mot de passe incorrect");
        setLoading(false);
      }
    } catch (e) {
      setError("Erreur réseau : " + (e as Error).message);
      setLoading(false);
    }
  }

  return (
    <div className="max-w-sm mx-auto mt-16">
      <h1 className="text-2xl font-bold">Admin</h1>
      <p className="text-sm text-muted mb-6">
        Cette page est réservée à l'équipe d'administration.
      </p>
      <form onSubmit={onSubmit} className="card space-y-4">
        <div>
          <label className="text-xs text-muted uppercase tracking-wide">
            Mot de passe
          </label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
            autoFocus
            className="w-full mt-1 px-3 py-2 rounded-xl border border-black/10 focus:border-ink focus:outline-none"
          />
        </div>
        {error && <div className="text-negative text-sm">{error}</div>}
        <button
          type="submit"
          disabled={loading || !password}
          className="btn-primary w-full disabled:opacity-40"
        >
          {loading ? "Connexion…" : "Se connecter"}
        </button>
      </form>
    </div>
  );
}
