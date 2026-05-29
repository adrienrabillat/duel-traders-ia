import type { Metadata } from "next";
import Image from "next/image";
import "./globals.css";

export const metadata: Metadata = {
  title: "Les Anges de l'IA",
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
          alt="Les Anges de l'IA"
          width={44}
          height={44}
          priority
          className="rounded-xl"
        />
        <div>
          <div className="font-semibold leading-tight">Les Anges de l'IA</div>
          <div className="text-xs text-muted">14 épisodes · 6 candidats</div>
        </div>
      </a>
      <nav className="flex gap-2 text-sm">
        <a href="/" className="btn-ghost">
          Live
        </a>
        <a href="/admin" className="btn-ghost">
          Admin
        </a>
      </nav>
    </header>
  );
}
