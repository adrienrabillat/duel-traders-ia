# Frontend — Duel de Traders IA

Dashboard Next.js (App Router) + Tailwind. Style minimaliste type fintech (inspiré Revolut / Linear), mobile-first.

## Stack

- Next.js 14 (App Router)
- TypeScript strict
- Tailwind CSS
- SWR pour le polling temps réel du backend

## Pages

- `/` : leaderboard live + feed de messages publics, polling 5s.
- `/admin` : ordres en attente avec boutons approuver/rejeter, kill switch.

## Démarrer en dev

```bash
cd frontend
cp .env.local.example .env.local
# Édite .env.local si ton backend tourne ailleurs que localhost:8000
npm install
npm run dev
```

Le backend FastAPI doit être lancé en parallèle :

```bash
cd backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Auth admin

Si `ADMIN_API_TOKEN` est défini côté backend, ajouter dans `.env.local` :

```
ADMIN_API_TOKEN=ton-token
ADMIN_PASSWORD=mot-de-passe-de-la-page-admin
```

Le navigateur n'appelle jamais le backend admin directement : il passe par le proxy
`app/api/admin/[...path]`, protégé par le cookie de session, qui ajoute le token côté serveur.

## Production (Vercel)

```bash
vercel deploy
```

Variables d'env à configurer dans le projet Vercel :
- `NEXT_PUBLIC_API_URL` : URL de ton backend Railway
- `ADMIN_API_TOKEN` et `ADMIN_PASSWORD` : pour l'administration (côté serveur uniquement)
- `NEXT_PUBLIC_DEMO_MODE=true` : rediffusion simulée sans backend (voir `lib/demo.ts`)

## À venir

- Page candidat individuel : courbe d'équité, stratégie courante, historique des trades.
- Streaming en direct du "thinking" via SSE.
- Mode replay accéléré.
- Visualisation des clans et confessionnaux en post-mortem.
