"""Clients HTTP pour les sources d'information externes : CoinGecko & Tavily.

Les deux clients exposent une interface simple et utilisent httpx en
asynchrone. Pour les tests, on injecte un MockTransport httpx, ce qui permet
de simuler les réponses API sans toucher au réseau.

CoinGecko : data structurée (prix, volumes, market caps, catégories).
Tavily    : recherche web orientée LLM, utilisée comme oracle de questions
            ouvertes anonymisées dans le feed partagé.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx


# =============================================================================
# Exceptions
# =============================================================================


class InfoClientError(Exception):
    pass


# =============================================================================
# Binance public ticker (live paper trading)
# =============================================================================


class BinancePublicClient:
    """Client minimaliste pour les endpoints publics de Binance Spot.

    Pas besoin de clé API : on lit juste les prix et le ticker 24h.
    Documentation : https://developers.binance.com/docs/binance-spot-api-docs/
    """

    BASE_URL = "https://api.binance.com"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self.BASE_URL, timeout=15.0
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get_spot_prices(self, symbols: list[str]) -> dict[str, Decimal]:
        """Renvoie le dernier prix spot pour chaque symbole (ex: BTCUSDC)."""
        params = [("symbols", "[" + ",".join(f'"{s}"' for s in symbols) + "]")]
        try:
            r = await self._client.get("/api/v3/ticker/price", params=params)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise InfoClientError(f"Binance price fetch failed: {e}") from e
        return {item["symbol"]: Decimal(item["price"]) for item in r.json()}

    async def get_24h_tickers(self, symbols: list[str]) -> dict[str, dict]:
        """Renvoie les tickers 24h (price, volume, change %) pour chaque symbole."""
        params = [("symbols", "[" + ",".join(f'"{s}"' for s in symbols) + "]")]
        try:
            r = await self._client.get("/api/v3/ticker/24hr", params=params)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise InfoClientError(f"Binance 24h ticker fetch failed: {e}") from e
        return {item["symbol"]: item for item in r.json()}


# =============================================================================
# CoinGecko
# =============================================================================


@dataclass(slots=True)
class CoinSnapshot:
    """Snapshot synthétique d'un coin à un instant donné."""

    coin_id: str
    symbol: str
    name: str
    current_price_usd: Decimal
    market_cap_usd: Decimal
    volume_24h_usd: Decimal
    price_change_24h_pct: Decimal | None
    categories: list[str]


class CoinGeckoClient:
    """Client minimaliste pour les endpoints CoinGecko gratuits.

    Documentation : https://docs.coingecko.com/reference/introduction
    """

    BASE_URL = "https://api.coingecko.com/api/v3"

    def __init__(
        self,
        api_key: str = "",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        headers = {}
        if api_key:
            headers["x-cg-demo-api-key"] = api_key
        self._client = client or httpx.AsyncClient(
            base_url=self.BASE_URL, headers=headers, timeout=15.0
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_universe_top_n(self, n: int = 50, vs_currency: str = "usd") -> list[CoinSnapshot]:
        """Top N coins par capitalisation. Renvoie un snapshot par coin."""
        resp = await self._client.get(
            "/coins/markets",
            params={
                "vs_currency": vs_currency,
                "order": "market_cap_desc",
                "per_page": n,
                "page": 1,
                "sparkline": "false",
                "price_change_percentage": "24h",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        out: list[CoinSnapshot] = []
        for row in data:
            try:
                out.append(
                    CoinSnapshot(
                        coin_id=row["id"],
                        symbol=row["symbol"].upper(),
                        name=row["name"],
                        current_price_usd=Decimal(str(row["current_price"])),
                        market_cap_usd=Decimal(str(row["market_cap"])),
                        volume_24h_usd=Decimal(str(row["total_volume"])),
                        price_change_24h_pct=(
                            Decimal(str(row["price_change_percentage_24h"]))
                            if row.get("price_change_percentage_24h") is not None
                            else None
                        ),
                        categories=[],  # rempli par get_categories si besoin
                    )
                )
            except (KeyError, TypeError) as e:
                raise InfoClientError(f"Unexpected CoinGecko row format: {e}") from e
        return out

    async def get_categories(self, coin_id: str) -> list[str]:
        """Récupère les categories d'un coin (utile pour le clustering)."""
        resp = await self._client.get(
            f"/coins/{coin_id}",
            params={"localization": "false", "tickers": "false", "market_data": "false"},
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("categories", [])

    async def get_fear_and_greed(self) -> dict[str, Any] | None:
        """L'index Fear & Greed n'est PAS sur CoinGecko mais sur alternative.me.

        Endpoint : https://api.alternative.me/fng/
        """
        async with httpx.AsyncClient(timeout=10.0) as c:
            resp = await c.get("https://api.alternative.me/fng/")
            if resp.status_code != 200:
                return None
            data = resp.json()
            entries = data.get("data") or []
            return entries[0] if entries else None


# =============================================================================
# Tavily
# =============================================================================


@dataclass(slots=True)
class TavilyAnswer:
    """Réponse Tavily : answer synthétisé + sources."""

    question: str
    answer: str
    sources: list[dict[str, str]]


class TavilyClient:
    """Client Tavily Search API.

    Documentation : https://docs.tavily.com/
    """

    BASE_URL = "https://api.tavily.com"

    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        if not api_key:
            raise InfoClientError("Tavily API key is required")
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(base_url=self.BASE_URL, timeout=20.0)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def search(
        self,
        question: str,
        max_results: int = 5,
        search_depth: str = "basic",
    ) -> TavilyAnswer:
        """Lance une recherche Tavily avec answer synthétisé."""
        resp = await self._client.post(
            "/search",
            json={
                "api_key": self._api_key,
                "query": question,
                "search_depth": search_depth,
                "include_answer": True,
                "max_results": max_results,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        sources: list[dict[str, str]] = []
        for r in data.get("results", []):
            sources.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": (r.get("content") or "")[:280],
            })
        return TavilyAnswer(
            question=question,
            answer=data.get("answer") or "Pas de réponse synthétisée.",
            sources=sources,
        )
