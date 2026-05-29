"""Liste les modèles réellement accessibles via chaque provider.

Utilise les endpoints /models (et un appel test pour Anthropic qui n'en expose pas)
pour confirmer ce qui existe vraiment côté API. Imprime un récap pour qu'on
puisse mettre à jour les noms de modèles dans run_paper.py.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import httpx

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass


PROVIDERS = [
    ("Anthropic", "https://api.anthropic.com/v1/models", "x-api-key", "ANTHROPIC_API_KEY", True),
    ("OpenAI", "https://api.openai.com/v1/models", "Authorization", "OPENAI_API_KEY", False),
    ("Mistral", "https://api.mistral.ai/v1/models", "Authorization", "MISTRAL_API_KEY", False),
    ("Gemini", "https://generativelanguage.googleapis.com/v1beta/models", None, "GOOGLE_API_KEY", False),
    ("xAI", "https://api.x.ai/v1/models", "Authorization", "XAI_API_KEY", False),
]


async def list_models(name, url, header_name, key_env, anthropic_style):
    key = os.environ.get(key_env, "").strip()
    if not key:
        return name, [f"({key_env} vide)"]
    headers = {}
    params = {}
    if name == "Gemini":
        url = f"{url}?key={key}"
    elif anthropic_style:
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    else:
        headers = {header_name: f"Bearer {key}"}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(url, headers=headers, params=params)
        if r.status_code != 200:
            return name, [f"HTTP {r.status_code} — {r.text[:200]}"]
        data = r.json()
        if name == "Gemini":
            models = [m["name"].split("/")[-1] for m in data.get("models", []) if "generateContent" in m.get("supportedGenerationMethods", [])]
        else:
            models = [m["id"] for m in data.get("data", [])]
        return name, models
    except Exception as e:
        return name, [f"Erreur : {e}"]


async def main():
    results = await asyncio.gather(*(list_models(*p) for p in PROVIDERS))
    for name, models in results:
        print(f"\n=== {name} ({len(models)} modèles) ===")
        # Filtrer les modèles « chat / instruct / pro / opus / large » pour ne pas
        # noyer dans des modèles d'embeddings, vision, etc.
        keywords_filter = (
            "opus", "sonnet", "haiku", "claude",
            "gpt", "o3", "o4",
            "mistral-large", "mistral-medium", "mistral-small",
            "gemini-2.5", "gemini-3", "gemini-2.0",
            "grok",
        )
        relevant = [m for m in models if any(k in m.lower() for k in keywords_filter)]
        if not relevant and len(models) <= 30:
            relevant = models
        for m in relevant:
            print(f"  - {m}")


if __name__ == "__main__":
    asyncio.run(main())
