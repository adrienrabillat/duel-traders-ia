"""Application FastAPI principale.

À lancer en dev :
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

En production, Railway lance ce module automatiquement (cf. Procfile à venir).
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import admin, public
from app.config import get_settings
from app.db import get_engine
from app.models import Base

logger = logging.getLogger(__name__)


def _run_runtime_in_background() -> bool:
    """Lance le runtime de paper trading en tâche de fond si RUN_RUNTIME=true."""
    return os.getenv("RUN_RUNTIME", "false").lower() in ("true", "1", "yes")


def _init_sentry() -> None:
    """Initialise Sentry si SENTRY_DSN est configuré.

    Sentry SDK 2.x détecte automatiquement FastAPI/Starlette quand elles
    sont importées avant init(), donc pas besoin de passer les intégrations
    explicitement.
    """
    dsn = get_settings().sentry_dsn
    if not dsn:
        return
    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=dsn,
            environment=get_settings().app_env,
            traces_sample_rate=0.1,  # 10% des requêtes tracées (à ajuster)
            send_default_pii=False,
            release="les-anges-de-lia@0.1.0",
        )
        logger.info("Sentry initialisé (env=%s)", get_settings().app_env)
    except ImportError:
        logger.warning("SENTRY_DSN défini mais sentry-sdk pas installé.")


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    """Initialise le schéma DB au démarrage et démarre le runtime si demandé.

    Le runtime de paper trading peut être démarré soit :
    1. Via la CLI `python -m scripts.run_paper` (process séparé).
    2. En définissant `RUN_RUNTIME=true` dans l'env (utile en prod Railway
       sur un même service).

    En option 2, l'orchestrateur tourne en tâche asyncio à côté de FastAPI.
    Si la FastAPI redémarre, le runtime redémarre aussi.
    """
    engine = get_engine()
    Base.metadata.create_all(engine)

    runtime_state: dict[str, object] = {}

    async def _start_runtime_deferred() -> None:
        """Construction + démarrage du runtime, déporté hors du chemin critique
        du lifespan pour ne pas bloquer le healthcheck."""
        try:
            # Import différé : run_paper a des dépendances que les tests n'ont pas
            from scripts.run_paper import bootstrap_and_build_loop

            loop_instance = await asyncio.to_thread(bootstrap_and_build_loop)
            runtime_state["loop"] = loop_instance
            logger.info("Runtime construit, démarrage de la boucle")
            await loop_instance.run_forever()
        except Exception:
            logger.exception("Runtime en arrière-plan a planté")

    runtime_task: asyncio.Task | None = None
    if _run_runtime_in_background():
        runtime_task = asyncio.create_task(_start_runtime_deferred())
        logger.info("Tâche runtime créée (démarrage différé)")

    try:
        yield
    finally:
        loop_instance = runtime_state.get("loop")
        if loop_instance is not None:
            loop_instance.stop()  # type: ignore[attr-defined]
        if runtime_task is not None:
            try:
                await asyncio.wait_for(runtime_task, timeout=10.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                runtime_task.cancel()
            except Exception:
                logger.exception("Erreur durant l'arrêt du runtime task")


def create_app() -> FastAPI:
    _init_sentry()
    settings = get_settings()
    app = FastAPI(
        title="Les Anges de l'IA",
        description="Backend de l'expérience de trading crypto en téléréalité IA.",
        version="0.1.0",
        lifespan=lifespan,
    )

    origins = (
        ["*"]
        if settings.cors_origins == "*"
        else [o.strip() for o in settings.cors_origins.split(",")]
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(public.router)
    app.include_router(admin.router)

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "les-anges-de-lia"}

    return app


app = create_app()
