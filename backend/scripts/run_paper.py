"""CLI : lance un paper trading complet avec les 5 candidats + Team.

Usage :
    python -m scripts.run_paper

Comportement :
- Pour chaque candidat individuel, tente d'instancier le VRAI provider à
  partir de la clé API dans .env. Si la clé est vide OU si le SDK n'est pas
  installé, on tombe en fallback MockProvider qui renvoie HOLD (l'expérience
  continue mais sans dépenser de tokens pour ce candidat).
- Le candidat Team utilise par défaut Anthropic.
- Toute la logique de cycle de vie (validation, exécution, OCO closures)
  est gérée par PaperTradingLoop.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from datetime import datetime, timezone
from decimal import Decimal

from app.config import get_settings
from app.db import get_engine, get_session_factory
from app.models import Base, Experiment
from app.services.binance_connector import PaperBinance
from app.services.info_clients import CoinGeckoClient, CoinSnapshot
from app.services.ledger import Ledger
from app.services.llm_gateway import (
    AnthropicProvider,
    GeminiProvider,
    LLMGateway,
    LLMProvider,
    MistralProvider,
    MockProvider,
    OpenAIProvider,
    XAIProvider,
)
from app.services.llm_gateway.base import ProviderError
from app.services.runtime import PaperTradingLoop, static_market_provider
from app.services.team_council import TeamMember

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("run_paper")


# =============================================================================
# Roster (nom, modèle, provider). À adapter selon les modèles dispos.
# =============================================================================

CANDIDATES = [
    ("Claude", "claude-opus-4-8", "anthropic"),
    ("ChatGPT", "gpt-5.5", "openai"),
    ("Mistral", "mistral-large-latest", "mistral"),
    ("Gemini", "gemini-3.1-pro-preview", "gemini"),
    ("Grok", "grok-4.3", "xai"),
]

TEAM_PROVIDER = "anthropic"

# Ordre des prises de parole dans le Conseil au tour 1. À chaque tour suivant
# on fait un shift circulaire (cf. team_council.run_council_deliberation).
TEAM_SPEAKING_ORDER = ["Mistral", "Claude", "ChatGPT", "Gemini", "Grok"]

# Capital initial de chaque candidat (USDC). En live paper trading sur de
# vrais prix Binance, 1000 USDC permet des positions exploitables (vs 10).
INITIAL_BALANCE_USDC = Decimal("1000")

# Univers tradable. Ces 3 paires sont assez liquides pour qu'on puisse
# faire confiance aux prix temps réel renvoyés par CoinGecko.
TRADABLE_SYMBOLS = ["BTCUSDC", "ETHUSDC", "SOLUSDC"]

SCRIPTED_HOLD = (
    '{"action": "HOLD", "reasoning_summary": "Mode fallback : pas de provider réel, '
    'décision par défaut HOLD."}'
)


# =============================================================================
# Provider factory : vrai SDK si la clé est là, Mock sinon
# =============================================================================


def build_provider(name: str) -> tuple[LLMProvider, str]:
    """Retourne (provider, label) — label = 'REAL' ou 'MOCK'."""
    settings = get_settings()
    key_by_name = {
        "anthropic": settings.anthropic_api_key,
        "openai": settings.openai_api_key,
        "mistral": settings.mistral_api_key,
        "gemini": settings.google_api_key,
        "xai": settings.xai_api_key,
    }
    key = key_by_name.get(name, "")
    if not key:
        return MockProvider(SCRIPTED_HOLD), "MOCK (clé vide)"
    try:
        if name == "anthropic":
            return AnthropicProvider(key), "REAL"
        if name == "openai":
            return OpenAIProvider(key), "REAL"
        if name == "mistral":
            return MistralProvider(key), "REAL"
        if name == "gemini":
            return GeminiProvider(key), "REAL"
        if name == "xai":
            return XAIProvider(key), "REAL"
    except ProviderError as e:
        logger.warning("Provider %s : SDK manquant ou invalide (%s) — fallback Mock", name, e)
        return MockProvider(SCRIPTED_HOLD), f"MOCK (SDK absent : {e})"
    return MockProvider(SCRIPTED_HOLD), "MOCK (inconnu)"


def build_gateway() -> LLMGateway:
    providers: dict[str, LLMProvider] = {}
    for name in ("anthropic", "openai", "mistral", "gemini", "xai"):
        provider, label = build_provider(name)
        providers[name] = provider
        logger.info("Provider %-10s : %s", name, label)
    return LLMGateway(providers, max_retries=2)


# =============================================================================
# Bootstrap DB
# =============================================================================


def _bootstrap_db_and_data() -> int:
    """Crée le schéma et amorce une expérience + les 6 candidats si absents.

    Si une expérience existe déjà mais que les noms de modèles ont changé
    (cas où on a corrigé un nom fictif → réel), on met à jour le champ
    `model` des candidats existants. La compta du Ledger n'est pas touchée.
    """
    engine = get_engine()
    Base.metadata.create_all(engine)
    factory = get_session_factory()
    with factory() as db:
        experiment = db.query(Experiment).filter(Experiment.status == "paper").first()
        if experiment is None:
            experiment = Experiment(
                name="Les Anges de l'IA — Saison 1",
                started_at=datetime.now(timezone.utc),
                status="paper",
                duration_days=14,
            )
            db.add(experiment)
            db.flush()
            ledger = Ledger(db)
            for name, model, _ in CANDIDATES:
                ledger.create_candidate(name, model, INITIAL_BALANCE_USDC)
            ledger.create_candidate(
                "Team", "consensus-5-models", INITIAL_BALANCE_USDC, kind="team"
            )
            db.commit()
            logger.info("Expérience initiale créée (id=%d)", experiment.id)
        else:
            # Sync des noms de modèles avec la config courante
            from app.models import Candidate

            current_models = {name: model for name, model, _ in CANDIDATES}
            current_models["Team"] = "consensus-5-models"
            updates = 0
            for cand in db.query(Candidate).all():
                wanted = current_models.get(cand.name)
                if wanted and cand.model != wanted:
                    logger.info("Sync model %s : %s → %s", cand.name, cand.model, wanted)
                    cand.model = wanted
                    updates += 1
            if updates:
                db.commit()
                logger.info("Sync %d candidats", updates)
        return experiment.id


# =============================================================================
# Main async
# =============================================================================


def make_live_market_provider(binance: PaperBinance):
    """Renvoie un market_provider asynchrone qui interroge CoinGecko (et
    alternative.me pour Fear&Greed) à chaque tick et synchronise PaperBinance
    avec les vrais prix spot.

    CoinGecko free tier : ~30 req/min — largement assez à un tick toutes les 30s.
    On utilise CoinGecko car Binance public bloque selon l'IP source (HTTP 451
    en EU/US depuis certains hosts cloud), alors que CoinGecko est accessible
    partout.

    Si CoinGecko répond mal (panne réseau, rate limit), on log et on garde les
    derniers prix connus (le tick continue, OCO inchangé).
    """
    cg = CoinGeckoClient()
    last_universe: list[CoinSnapshot] = []
    last_prices: dict[str, Decimal] = {}
    # Coins CoinGecko qu'on suit et leur symbole Binance équivalent
    coin_to_binance = {"bitcoin": "BTCUSDC", "ethereum": "ETHUSDC", "solana": "SOLUSDC"}

    async def _provider():
        nonlocal last_universe, last_prices
        try:
            top = await cg.get_universe_top_n(20)
        except Exception as e:  # pragma: no cover - réseau
            logger.warning("CoinGecko fetch failed (%s) — fallback derniers prix", e)
            return last_universe, last_prices, None

        universe: list[CoinSnapshot] = []
        prices: dict[str, Decimal] = {}
        for snap in top:
            symbol = coin_to_binance.get(snap.coin_id)
            if symbol is None:
                continue  # on garde uniquement les 3 paires tradables
            prices[symbol] = snap.current_price_usd
            # Sync PaperBinance pour que les OCO se déclenchent sur le vrai prix
            binance.set_price(symbol, snap.current_price_usd)
            universe.append(snap)

        # Fear & Greed via alternative.me (silent fail si indispo)
        fear_greed: int | None = None
        try:
            fng = await cg.get_fear_and_greed()
            if fng and "value" in fng:
                fear_greed = int(fng["value"])
        except Exception:  # pragma: no cover
            pass

        last_universe, last_prices = universe, prices
        return universe, prices, fear_greed

    return _provider


def build_loop(experiment_id: int) -> PaperTradingLoop:
    """Construit une PaperTradingLoop prête à tourner, sans la démarrer.

    Réutilisable par la CLI (run_paper) et par le lifespan FastAPI en prod.
    Utilise Binance public pour les prix en live (paper trading réaliste).
    """
    binance = PaperBinance()
    # Prix initiaux par sécurité — seront overridés au premier tick
    binance.set_price("BTCUSDC", Decimal("100000"))
    binance.set_price("ETHUSDC", Decimal("3500"))
    binance.set_price("SOLUSDC", Decimal("180"))

    gateway = build_gateway()
    provider_map = {name: provider for name, _, provider in CANDIDATES}
    provider_map["Team"] = TEAM_PROVIDER

    factory = get_session_factory()
    with factory() as db:
        experiment = db.get(Experiment, experiment_id)
        assert experiment is not None

    # Le Team est composé des 5 mêmes modèles, en instances dédiées (gateway
    # call séparé). L'ordre suit TEAM_SPEAKING_ORDER, pas CANDIDATES.
    by_name = {name: (name, model, provider) for name, model, provider in CANDIDATES}
    team_members = [
        TeamMember(name=name, model=model, provider=provider)
        for speaker in TEAM_SPEAKING_ORDER
        for (name, model, provider) in [by_name[speaker]]
    ]

    return PaperTradingLoop(
        session_factory=factory,
        experiment=experiment,
        binance=binance,
        gateway=gateway,
        provider_map=provider_map,
        market_provider=make_live_market_provider(binance),
        tick_seconds=get_settings().runtime_tick_seconds,
        team_members=team_members,
    )


def bootstrap_and_build_loop() -> PaperTradingLoop:
    """Bootstrap DB + candidats + build de la loop, prêt à run_forever()."""
    experiment_id = _bootstrap_db_and_data()
    return build_loop(experiment_id)


async def main() -> None:
    loop = bootstrap_and_build_loop()

    def _shutdown(*_args):
        logger.info("Signal reçu, arrêt propre du runtime…")
        loop.stop()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    logger.info(
        "Démarrage du runtime — tick=%ds — Ctrl+C pour arrêter",
        get_settings().runtime_tick_seconds,
    )
    await loop.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
