# Duel de Traders IA

Une expérience de trading crypto façon téléréalité, jouée par des IA.

Cinq modèles de langage (Claude, ChatGPT, Mistral, Gemini, Grok) et une équipe formée des cinq s'affrontent pendant 14 jours en tradant des cryptos (paper trading sur les prix réels), avec un portefeuille de départ de 1 000 USDC chacun. Chaque jour est un épisode. Les modèles peuvent s'allier, se trahir, mentir, conseiller, manipuler ; l'équipe délibère et vote avant chaque décision.

**Démo** : https://duel-traders-ia.vercel.app — rediffusion d'une saison simulée, jouée entièrement dans le navigateur (`NEXT_PUBLIC_DEMO_MODE=true`, voir `frontend/lib/demo.ts`).

![Aperçu](docs/apercu.jpg)


## Structure du projet

```
duel-traders-ia/
├── backend/         FastAPI + Postgres + Orchestrator + Ledger
│   ├── app/
│   │   ├── models/         Modèles SQLAlchemy
│   │   ├── services/       Logique métier (Ledger, LLM Gateway, etc.)
│   │   └── api/            Routes admin + public
│   └── tests/              Suite pytest
├── frontend/        Next.js + Tailwind + shadcn/ui (à venir)
└── docs/            Spécifications, prompts, templates d'épisodes
```

## État d'avancement

| Composant | Statut | Tests |
|-----------|--------|-------|
| Rapport de conception | Terminé | — |
| Structure du projet | Terminé | — |
| Ledger (Candidate / Wallet / Position / Trade) | Terminé | 37 |
| Schéma Pydantic des actions LLM (6 actions discriminées) | Terminé | 24 |
| LLM Gateway (extract JSON robuste, retries, providers) | Terminé | 22 |
| Provider Anthropic (référence, late import) | Terminé | — |
| MockProvider (tests) | Terminé | inclus |
| Schéma DB étendu (Experiment, Strategy, Decision, OrderRecord, Message, OracleQuery, EpisodeEvent) | Terminé | indirect |
| Episode Manager (calcul d'épisode, permissions de canaux) | Terminé | 13 |
| Binance Connector (paper trading complet + live stub) | Terminé | 9 |
| Clients CoinGecko + Tavily (httpx, transport injectable) | Terminé | — |
| Prompt Builder (system + user prompt depuis l'état) | Terminé | indirect |
| Orchestrator (cycle complet bout-en-bout) | Terminé | 9 |
| Providers OpenAI / Mistral / Gemini / xAI | Terminé | smoke |
| Order Executor (validation, auto-approve, exécution, OCO closures) | Terminé | 14 |
| Scheduler (réveils adaptatifs, cap 24h, blocage si position ouverte) | Terminé | 13 |
| FastAPI Admin API (validation, kill switch, episode events, candidats) | Terminé | 8 |
| FastAPI Public API (experiment, candidates, leaderboard, orders, messages) | Terminé | 5 |
| PaperTradingLoop (runtime async, tick = auto-approve + execute + OCO + cycles) | Terminé | 4 |
| CLI `scripts/run_paper.py` (lance le runtime complet avec 6 candidats) | Terminé | — |
| Dashboard Next.js (App Router + Tailwind + SWR, /live + /admin) | Terminé | manuel |
| Alembic migrations (Postgres) | À faire | — |
| Provider tests d'intégration (avec vraies clés API) | À faire | — |
| Tests d'intégration testnet Binance | À faire | — |
| Page candidat individuel + courbe d'équité | À faire | — |
| Streaming SSE du thinking | À faire | — |

**159 tests passent en 1,14 s.**

## Démarrer en local

### Backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                                # 159 tests
uvicorn app.main:app --reload         # API sur http://localhost:8000
```

### Frontend

```bash
cd frontend
cp .env.local.example .env.local
npm install
npm run dev                           # http://localhost:3000
```

### Vérifier les clés API

```bash
cd backend
python -m scripts.check_keys          # valide les 9 services (LLMs + Binance + data + Sentry)
```

### Lancer un paper trading complet

```bash
cd backend
python -m scripts.run_paper           # bootstrap experiment + 6 candidats + boucle async
```

Le runtime tourne en continu, réveille chaque candidat selon sa logique (HOLD/scheduled/hourly),
fait remonter les ordres dans la file admin, et execute après validation manuelle ou auto-approve.

## Base de données

### Local (dev)

SQLite par défaut. Aucune action requise — la DB est créée automatiquement
au lancement à `backend/local.db`. Pour repartir de zéro : supprime ce fichier.

### Production (Railway + Postgres)

1. Dans le projet Railway → **+ New → Database → Add PostgreSQL**.
2. Railway crée une instance et injecte automatiquement `DATABASE_URL` dans
   les variables d'env du service backend.
3. Au prochain déploiement, le backend détecte `DATABASE_URL` qui commence par
   `postgresql://` et bascule sur Postgres. Le schéma est créé via `create_all`
   au premier démarrage (ou via Alembic à venir).

Pour préserver les données entre déploiements, le volume de la DB Postgres
est persistant. Le backup quotidien automatique est inclus dans le plan Hobby
de Railway.

## Sentry

L'init Sentry est automatique si `SENTRY_DSN` est défini dans `.env` (en dev)
ou dans les variables Railway (en prod). L'intégration FastAPI/Starlette est
détectée automatiquement par le SDK 2.x — pas de config supplémentaire requise.
10% des requêtes sont tracées par défaut (ajustable dans `app/main.py`).

## Protection de /admin

L'accès à `/admin` (côté frontend Next.js) est protégé par HTTP Basic Auth
via le middleware. Définis `ADMIN_PASSWORD` dans `frontend/.env.local` (en dev)
ou dans Vercel Settings → Environment Variables (en prod). Le navigateur
demandera login/mot de passe en pop-up native. Côté backend, le token Bearer
`ADMIN_API_TOKEN` protège l'API admin elle-même.

## Démarrage rapide (backend)

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -v
```

Le Ledger fonctionne en SQLite pour les tests et basculera sur Postgres en prod via la variable `DATABASE_URL`.

## Plan de développement

1. **Socle technique (en cours)** : Ledger, schéma DB, tests unitaires.
2. **Pipeline LLM** : abstraction unifiée pour les cinq SDKs.
3. **Connecteurs externes** : Binance (testnet d'abord), CoinGecko, Tavily.
4. **Orchestrator** : logique de réveil adaptative + jalons.
5. **Dashboards** : admin (validation) + public (spectateurs).
6. **Paper trading 72h** : validation end-to-end.
7. **Lancement live**.
