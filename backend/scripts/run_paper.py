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
from app.services.info_clients import CoinSnapshot
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
    ("Claude", "claude-opus-4-7", "anthropic"),
    ("ChatGPT", "gpt-5.5", "openai"),
    ("Mistral", "mistral-large-3", "mistral"),
    ("Gemini", "gemini-3.1-pro", "gemini"),
    ("Grok", "grok-4.20", "xai"),
]

TEAM_PROVIDER = "anthropic"

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
    """Crée le schéma et amorce une expérience + les 6 candidats si absents."""
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
                ledger.create_candidate(name, model, Decimal("10"))
            ledger.create_candidate(
                "Team", "consensus-5-models", Decimal("10"), kind="team"
            )
            db.commit()
            logger.info("Expérience initiale créée (id=%d)", experiment.id)
        return experiment.id


# =============================================================================
# Main async
# =============================================================================


def build_loop(experiment_id: int) -> PaperTradingLoop:
    """Construit une PaperTradingLoop prête à tourner, sans la démarrer.

    Réutilisable par la CLI (run_paper) et par le lifespan FastAPI en prod.
    """
    binance = PaperBinance()
    binance.set_price("BTCUSDC", Decimal("100000"))
    binance.set_price("ETHUSDC", Decimal("3500"))
    binance.set_price("SOLUSDC", Decimal("180"))

    gateway = build_gateway()
    provider_map = {name: provider for name, _, provider in CANDIDATES}
    provider_map["Team"] = TEAM_PROVIDER

    universe = [
        CoinSnapshot(
            coin_id="bitcoin",
            symbol="BTC",
            name="Bitcoin",
            current_price_usd=Decimal("100000"),
            market_cap_usd=Decimal("2000000000000"),
            volume_24h_usd=Decimal("50000000000"),
            price_change_24h_pct=Decimal("1.5"),
            categories=["majors"],
        ),
        CoinSnapshot(
            coin_id="ethereum",
            symbol="ETH",
            name="Ethereum",
            current_price_usd=Decimal("3500"),
            market_cap_usd=Decimal("420000000000"),
            volume_24h_usd=Decimal("20000000000"),
            price_change_24h_pct=Decimal("0.8"),
            categories=["majors"],
        ),
        CoinSnapshot(
            coin_id="solana",
            symbol="SOL",
            name="Solana",
            current_price_usd=Decimal("180"),
            market_cap_usd=Decimal("85000000000"),
            volume_24h_usd=Decimal("3000000000"),
            price_change_24h_pct=Decimal("2.4"),
            categories=["L1"],
        ),
    ]
    prices = {
        "BTCUSDC": Decimal("100000"),
        "ETHUSDC": Decimal("3500"),
        "SOLUSDC": Decimal("180"),
    }

    factory = get_session_factory()
    with factory() as db:
        experiment = db.get(Experiment, experiment_id)
        assert experiment is not None

    # Le Team est composé des 5 mêmes modèles, en instances dédiées (gateway
    # call séparé). Voir la section 2.2 du rapport.
    team_members = [
        TeamMember(name=name, model=model, provider=provider)
        for name, model, provider in CANDIDATES
    ]

    return PaperTradingLoop(
        session_factory=factory,
        experiment=experiment,
        binance=binance,
        gateway=gateway,
        provider_map=provider_map,
        market_provider=static_market_provider(universe, prices, fear_greed=55),
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
