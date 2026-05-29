"""Configuration applicative chargée depuis les variables d'environnement."""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration globale du backend.

    Toutes les valeurs sont chargées depuis le fichier .env ou les variables
    d'environnement du process. Les secrets ne doivent JAMAIS être commités.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- App ---
    app_env: str = "dev"
    log_level: str = "INFO"

    # --- Base de données ---
    database_url: str = "sqlite:///./local.db"

    # --- Binance ---
    binance_api_key: str = ""
    binance_api_secret: str = ""
    binance_testnet: bool = True

    # --- LLMs ---
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    mistral_api_key: str = ""
    google_api_key: str = ""
    xai_api_key: str = ""

    # --- Données de marché ---
    coingecko_api_key: str = ""
    tavily_api_key: str = ""

    # --- Observabilité ---
    sentry_dsn: str = ""

    # --- API ---
    admin_api_token: str = ""  # vide = mode dev sans auth
    cors_origins: str = "*"

    # --- Runtime ---
    runtime_tick_seconds: int = 30


# --- Constantes métier (non configurables via env) ---

# Frais Binance Spot en mode taker standard (sans BNB)
BINANCE_FEE_RATE: Decimal = Decimal("0.001")

# Minimum d'ordre Binance pour les paires USDC (en USDC)
MIN_ORDER_USDC: Decimal = Decimal("5.0")

# Capital initial par candidat actif
INITIAL_CAPITAL_USDC: Decimal = Decimal("10.0")

# Quantification monétaire (8 décimales — précision Binance pour les paires fiat)
MONEY_QUANTUM: Decimal = Decimal("0.00000001")

# Quantification des quantités d'actifs (12 décimales — pour les petits jetons)
ASSET_QUANTUM: Decimal = Decimal("0.000000000001")


@lru_cache
def get_settings() -> Settings:
    """Renvoie la config (mise en cache)."""
    return Settings()
