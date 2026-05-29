"""Valide les clés API du .env en faisant un appel minimal à chaque service.

Usage :
    python -m scripts.check_keys

Le script ne consomme presque rien :
- LLMs : il LISTE juste les modèles (endpoint /models) → 0 token.
- Anthropic n'a pas de /models, on fait donc un appel de 1 token sur Haiku.
- Binance : appel signé sur /api/v3/account → vérifie clé + IP whitelist + permissions.
- CoinGecko : un ping avec la clé.
- Tavily : un search d'un mot.
- Sentry : valide le format du DSN (pas d'envoi d'événement par défaut).

Affiche un récap final par service avec OK / FAIL + explication.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urlparse

import httpx

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass


# =============================================================================
# Helpers
# =============================================================================


@dataclass(slots=True)
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"
DIM = "\033[2m"


def ok(name: str, detail: str = "") -> CheckResult:
    return CheckResult(name, True, detail)


def fail(name: str, detail: str) -> CheckResult:
    return CheckResult(name, False, detail)


def env(key: str) -> str:
    return os.getenv(key, "").strip()


# =============================================================================
# Checks
# =============================================================================


async def check_anthropic(client: httpx.AsyncClient) -> CheckResult:
    key = env("ANTHROPIC_API_KEY")
    if not key:
        return fail("Anthropic", "ANTHROPIC_API_KEY vide")
    try:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "ok"}],
            },
            timeout=15.0,
        )
    except Exception as e:
        return fail("Anthropic", f"Réseau : {e}")
    if r.status_code == 200:
        return ok("Anthropic", "auth OK, modèle Haiku 4.5 répond")
    if r.status_code == 401:
        return fail("Anthropic", "401 : clé invalide")
    if r.status_code == 404:
        # Modèle inconnu : la clé est valide, mais le modèle n'existe pas → essayons un fallback
        return ok(
            "Anthropic",
            f"clé valide, modèle test 404 (essaie claude-3-5-haiku) — {r.text[:100]}",
        )
    return fail("Anthropic", f"HTTP {r.status_code} : {r.text[:200]}")


async def check_openai(client: httpx.AsyncClient) -> CheckResult:
    key = env("OPENAI_API_KEY")
    if not key:
        return fail("OpenAI", "OPENAI_API_KEY vide")
    try:
        r = await client.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=15.0,
        )
    except Exception as e:
        return fail("OpenAI", f"Réseau : {e}")
    if r.status_code == 200:
        models = r.json().get("data", [])
        return ok("OpenAI", f"{len(models)} modèles accessibles")
    if r.status_code == 401:
        return fail("OpenAI", "401 : clé invalide")
    return fail("OpenAI", f"HTTP {r.status_code} : {r.text[:200]}")


async def check_mistral(client: httpx.AsyncClient) -> CheckResult:
    key = env("MISTRAL_API_KEY")
    if not key:
        return fail("Mistral", "MISTRAL_API_KEY vide")
    try:
        r = await client.get(
            "https://api.mistral.ai/v1/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=15.0,
        )
    except Exception as e:
        return fail("Mistral", f"Réseau : {e}")
    if r.status_code == 200:
        models = r.json().get("data", [])
        return ok("Mistral", f"{len(models)} modèles accessibles")
    if r.status_code == 401:
        return fail("Mistral", "401 : clé invalide")
    return fail("Mistral", f"HTTP {r.status_code} : {r.text[:200]}")


async def check_gemini(client: httpx.AsyncClient) -> CheckResult:
    key = env("GOOGLE_API_KEY")
    if not key:
        return fail("Gemini", "GOOGLE_API_KEY vide")
    try:
        r = await client.get(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={key}",
            timeout=15.0,
        )
    except Exception as e:
        return fail("Gemini", f"Réseau : {e}")
    if r.status_code == 200:
        models = r.json().get("models", [])
        return ok("Gemini", f"{len(models)} modèles accessibles")
    if r.status_code in (401, 403):
        return fail("Gemini", f"{r.status_code} : clé invalide ou API non activée")
    return fail("Gemini", f"HTTP {r.status_code} : {r.text[:200]}")


async def check_xai(client: httpx.AsyncClient) -> CheckResult:
    key = env("XAI_API_KEY")
    if not key:
        return fail("xAI", "XAI_API_KEY vide")
    try:
        r = await client.get(
            "https://api.x.ai/v1/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=15.0,
        )
    except Exception as e:
        return fail("xAI", f"Réseau : {e}")
    if r.status_code == 200:
        models = r.json().get("data", [])
        return ok("xAI", f"{len(models)} modèles accessibles")
    if r.status_code == 401:
        return fail("xAI", "401 : clé invalide")
    return fail("xAI", f"HTTP {r.status_code} : {r.text[:200]}")


async def check_binance(client: httpx.AsyncClient) -> CheckResult:
    key = env("BINANCE_API_KEY")
    secret = env("BINANCE_API_SECRET")
    if not key or not secret:
        return fail("Binance", "Clé ou secret vide")

    testnet = env("BINANCE_TESTNET").lower() in ("true", "1", "yes")
    base = "https://testnet.binance.vision" if testnet else "https://api.binance.com"

    ts = int(time.time() * 1000)
    query = f"timestamp={ts}"
    signature = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    try:
        r = await client.get(
            f"{base}/api/v3/account?{query}&signature={signature}",
            headers={"X-MBX-APIKEY": key},
            timeout=15.0,
        )
    except Exception as e:
        return fail("Binance", f"Réseau : {e}")

    suffix = " (testnet)" if testnet else ""
    if r.status_code == 200:
        data = r.json()
        usdc_balance = next(
            (b for b in data.get("balances", []) if b["asset"] == "USDC"),
            None,
        )
        free = usdc_balance["free"] if usdc_balance else "?"
        return ok("Binance", f"auth OK{suffix} · USDC libre : {free}")
    if r.status_code == 401:
        return fail("Binance", "401 : clé invalide")
    if r.status_code == 418 or r.status_code == 429:
        return fail("Binance", f"{r.status_code} : rate limit / ban temporaire")
    if r.status_code == 403:
        try:
            data = r.json()
            msg = data.get("msg", "")
        except Exception:
            msg = r.text[:150]
        if "ip" in msg.lower() or "address" in msg.lower():
            return fail("Binance", f"IP non whitelistée ({msg})")
        return fail("Binance", f"403 : {msg}")
    return fail("Binance", f"HTTP {r.status_code}{suffix} : {r.text[:200]}")


async def check_coingecko(client: httpx.AsyncClient) -> CheckResult:
    key = env("COINGECKO_API_KEY")
    headers = {"x-cg-demo-api-key": key} if key else {}
    try:
        r = await client.get(
            "https://api.coingecko.com/api/v3/ping",
            headers=headers,
            timeout=15.0,
        )
    except Exception as e:
        return fail("CoinGecko", f"Réseau : {e}")
    if r.status_code == 200:
        return ok("CoinGecko", "ping OK avec clé Demo")
    if r.status_code == 401:
        return fail("CoinGecko", "401 : clé invalide")
    return fail("CoinGecko", f"HTTP {r.status_code} : {r.text[:200]}")


async def check_tavily(client: httpx.AsyncClient) -> CheckResult:
    key = env("TAVILY_API_KEY")
    if not key:
        return fail("Tavily", "TAVILY_API_KEY vide")
    try:
        r = await client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": key,
                "query": "ping",
                "max_results": 1,
                "include_answer": False,
            },
            timeout=20.0,
        )
    except Exception as e:
        return fail("Tavily", f"Réseau : {e}")
    if r.status_code == 200:
        return ok("Tavily", "recherche test OK")
    if r.status_code in (401, 403):
        return fail("Tavily", f"{r.status_code} : clé invalide")
    return fail("Tavily", f"HTTP {r.status_code} : {r.text[:200]}")


def check_sentry() -> CheckResult:
    dsn = env("SENTRY_DSN")
    if not dsn:
        return ok("Sentry", "non configuré (OK, optionnel)")
    try:
        parsed = urlparse(dsn)
        if parsed.scheme not in ("http", "https"):
            return fail("Sentry", "scheme invalide (attendu http(s))")
        if not parsed.hostname:
            return fail("Sentry", "hostname manquant")
        if not parsed.username:
            return fail("Sentry", "public key manquant (avant le @)")
        if not parsed.path or parsed.path == "/":
            return fail("Sentry", "project ID manquant (après le /)")
        return ok("Sentry", f"DSN bien formé sur {parsed.hostname}")
    except Exception as e:
        return fail("Sentry", f"Parse error : {e}")


# =============================================================================
# Main
# =============================================================================


async def main() -> int:
    print(f"\n{DIM}Validation des clés API du .env…{RESET}\n")

    async with httpx.AsyncClient() as client:
        checks: list[Callable[[], Awaitable[CheckResult]] | CheckResult] = [
            check_anthropic(client),
            check_openai(client),
            check_mistral(client),
            check_gemini(client),
            check_xai(client),
            check_binance(client),
            check_coingecko(client),
            check_tavily(client),
        ]
        results = await asyncio.gather(*checks)

    results = list(results) + [check_sentry()]

    width = max(len(r.name) for r in results)
    failed = 0
    for r in results:
        if r.ok:
            tag = f"{GREEN}OK{RESET}"
        else:
            tag = f"{RED}FAIL{RESET}"
            failed += 1
        print(f"  [{tag}] {r.name:<{width}}  {DIM}{r.detail}{RESET}")

    print()
    if failed == 0:
        print(f"{GREEN}✓ Toutes les clés sont opérationnelles.{RESET}\n")
    else:
        print(
            f"{RED}✗ {failed} échec(s) sur {len(results)}. "
            f"Vérifie les clés concernées avant de lancer un paper trading.{RESET}\n"
        )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
