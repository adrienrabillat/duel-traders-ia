import type { Metadata } from "next";
import Image from "next/image";
import { DEMO_MODE } from "@/lib/api";
import "./globals.css";

export const metadata: Metadata = {
  title: "Duel de Traders IA",
  description:
    "Cinq modèles de langage en compétition sur du trading crypto pendant 14 épisodes.",
  icons: {
    icon: "/logo.png",
    apple: "/logo.png",
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="fr">
      <body>
        <div className="max-w-5xl mx-auto px-4 sm:px-6 py-6">
          {DEMO_MODE && <DemoBanner />}
          <Header />
          <main className="mt-6 pb-12">{children}</main>
        </div>
      </body>
    </html>
  );
}

function Header() {
  return (
    <header className="flex items-center justify-between">
      <a href="/" className="flex items-center gap-3">
        <Image
          src="/logo.png"
          alt="Duel de Traders IA"
          width={44}
          height={44}
          priority
          className="rounded-xl"
        />
        <div>
          <div className="font-semibold leading-tight">Duel de Traders IA</div>
          <div className="text-xs text-muted">14 épisodes · 6 candidats</div>
        </div>
      </a>
      <nav className="flex gap-2 text-sm">
        <a href="/" className="btn-ghost">
          Live
        </a>
        {!DEMO_MODE && (
          <a href="/admin" className="btn-ghost">
            Admin
          </a>
        )}
      </nav>
    </header>
  );
}

function DemoBanner() {
  return (
    <div className="card mb-6 text-sm">
      <span className="font-semibold">Mode démo</span> — rediffusion d'une saison simulée :
      les prix, décisions et délibérations sont un scénario rejoué dans le navigateur.
      La version réelle tourne avec un backend FastAPI + Postgres et les API de cinq LLM.{" "}
      <a
        href="https://github.com/adrienrabillat/duel-traders-ia"
        className="underline"
        target="_blank"
        rel="noopener"
      >
        Voir le code
      </a>
    </div>
  );
}
