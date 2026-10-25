# Déploiement — Duel de Traders IA

Guide step-by-step pour passer du local au prod (Railway + Vercel + Postgres).

## Architecture cible

```
┌─────────────────────────────┐         ┌──────────────────────────┐
│  Vercel (free)              │  HTTPS  │  Railway (~$10/mois)     │
│  - Next.js frontend         ├────────►│  - FastAPI backend       │
│  - /admin protégé Basic     │         │  - Runtime paper trading │
│    Auth via middleware      │         │  - Postgres add-on       │
└─────────────────────────────┘         └──────────────────────────┘
```

## Pré-requis

- Code committé sur GitHub (repo public ou privé, peu importe)
- Compte Railway (https://railway.app)
- Compte Vercel (https://vercel.com)
- Toutes les clés API du `.env` local validées via `python -m scripts.check_keys`

---

## Partie 1 — Railway (backend + Postgres)

### 1. Créer le projet Railway

1. Va sur https://railway.app/new → **Deploy from GitHub repo**
2. Sélectionne le repo `duel-traders-ia`
3. Quand Railway demande quel dossier servir, choisis `backend/`

Railway détecte `pyproject.toml` + `nixpacks.toml` et installe automatiquement
toutes les dépendances (y compris les SDKs LLM via l'extra `[llms]`).

### 2. Ajouter Postgres

Dans le dashboard du projet Railway :
- Clic **+ New** → **Database** → **Add PostgreSQL**
- Railway crée une instance, génère une `DATABASE_URL` et **l'injecte automatiquement** comme variable d'env dans ton service backend

C'est la seule étape DB nécessaire. Le code détecte le `postgresql://` au démarrage
et crée les tables via `Base.metadata.create_all()` lors du premier boot.

### 3. Configurer les variables d'environnement backend

Dans le service backend Railway → **Variables** → ajoute une par une :

| Variable | Valeur |
|----------|--------|
| `DATABASE_URL` | (déjà injectée par Postgres, ne pas écraser) |
| `BINANCE_API_KEY` | Ta clé testnet (commence par les 64 chars HMAC) |
| `BINANCE_API_SECRET` | Ton secret testnet |
| `BINANCE_TESTNET` | `true` |
| `ANTHROPIC_API_KEY` | Ta clé |
| `OPENAI_API_KEY` | Ta clé |
| `MISTRAL_API_KEY` | Ta clé |
| `GOOGLE_API_KEY` | Ta clé |
| `XAI_API_KEY` | Ta clé |
| `COINGECKO_API_KEY` | Ta clé Demo |
| `TAVILY_API_KEY` | Ta clé |
| `SENTRY_DSN` | Ton DSN |
| `ADMIN_API_TOKEN` | Génère : `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `CORS_ORIGINS` | `https://duel-traders-ia.vercel.app` (à mettre une fois Vercel déployé) |
| `RUN_RUNTIME` | `true` (active la boucle de paper trading en background) |
| `APP_ENV` | `production` |
| `LOG_LEVEL` | `INFO` |

### 4. Obtenir l'URL publique

Dans **Settings** → **Networking** → clic **Generate Domain**
Railway te donne une URL type `https://duel-traders-ia-production.up.railway.app`

Teste-la : `https://<ton-url>/health` doit retourner `{"status":"ok"}`.

### 5. Restreindre Binance par IP

Une fois Railway déployé, va sur Railway → ton service → **Settings** → trouve l'IP de sortie de Railway (ou utilise un outpost statique si tu as le plan Pro).
Puis sur Binance testnet (ou mainnet plus tard) → **API Management** →
Ta clé → **Restrict access to trusted IPs only** → ajoute l'IP Railway.

⚠️ Sur Railway Hobby plan, les IPs sortantes ne sont pas statiques. Soit tu prends
le plan Pro pour avoir une IP fixe, soit tu désactives l'IP whitelist sur la clé
testnet (acceptable car risque limité au testnet — JAMAIS sur mainnet sans IP fixe).

---

## Partie 2 — Vercel (frontend)

### 1. Importer le projet

Sur https://vercel.com/new → **Import Git Repository** → sélectionne le repo

Vercel détecte Next.js. Quand il demande le dossier racine, choisis `frontend/`.

### 2. Configurer les variables d'environnement

Dans Vercel → **Settings** → **Environment Variables** → ajoute :

| Variable | Valeur | Visibility |
|----------|--------|------------|
| `NEXT_PUBLIC_API_URL` | URL Railway, ex: `https://duel-traders-ia-production.up.railway.app` | Production |
| `ADMIN_API_TOKEN` | Même valeur que `ADMIN_API_TOKEN` côté Railway (utilisée par le proxy `/api/admin`, jamais exposée au navigateur) | Production |
| `ADMIN_PASSWORD` | Mot de passe pour Basic Auth sur `/admin`. À garder secret. | Production |

> Note : `NEXT_PUBLIC_*` sont exposées au navigateur. `ADMIN_PASSWORD` et `ADMIN_API_TOKEN`
> ne le sont pas (pas de préfixe `NEXT_PUBLIC_`) : elles restent côté serveur.

### 3. Déployer

**Deploy** → Vercel build et publie sur `https://<ton-projet>.vercel.app`

Tu peux ensuite ajouter un domaine custom (gratuit avec Vercel) dans **Settings** → **Domains**.

### 4. Mettre à jour CORS sur Railway

Retourne dans Railway → backend → Variables :
- `CORS_ORIGINS` = `https://<ton-projet>.vercel.app`

Redéploie. C'est maintenant verrouillé sur ton domaine Vercel.

---

## Partie 3 — Premier test post-déploiement

1. Va sur l'URL Vercel.
2. Tu devrais voir le leaderboard avec les 6 candidats (créés automatiquement par
   `bootstrap_and_build_loop()` au démarrage du runtime).
3. Le runtime tourne en background : toutes les 30s, il vérifie qui réveiller et
   lance un cycle. Au premier cycle, chaque candidat va écrire sa stratégie initiale.
4. Va sur `/admin` → le navigateur te demande login/password (Basic Auth) →
   utilise le mot de passe `ADMIN_PASSWORD`.
5. Tu vas voir les premiers ordres remonter au fur et à mesure.

---

## Itérer en prod

À chaque `git push` sur la branche principale :
- Railway redéploie automatiquement le backend
- Vercel redéploie automatiquement le frontend

Pas de downtime grâce au déploiement bleu/vert de Vercel.
Sur Railway, le service redémarre (~10s de coupure côté API).

## Surveillance

- **Sentry** : les erreurs apparaissent automatiquement (init dans `main.py`)
- **Railway logs** : onglet "Logs" du service backend
- **Vercel logs** : onglet "Functions" → choisis ton déploiement
- **UptimeRobot** (optionnel, free) : ping `https://<railway-url>/health` toutes les 5 min
  → email/SMS si le backend tombe

## Coûts attendus

| Service | Coût/mois |
|---------|-----------|
| Railway Hobby (backend + Postgres) | $5 + usage (~$3-8 selon trafic) |
| Vercel Hobby (frontend) | $0 |
| Sentry (free tier) | $0 |
| APIs LLM (variable) | $20-200 selon volume de réflexions |
| **Total** | **~$15-30/mois hors LLMs** |

## Rollback en cas de bug

- **Railway** : onglet **Deployments** → clic sur un ancien build → **Redeploy**
- **Vercel** : onglet **Deployments** → clic sur un ancien build → **Promote to Production**

Les deux préservent les variables d'env. La DB n'est pas affectée par un rollback
code (seules les migrations le seraient — d'où Alembic à venir).
