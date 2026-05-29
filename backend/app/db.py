"""Initialisation SQLAlchemy.

Le moteur et la factory de sessions sont créés une seule fois et réutilisés
par l'ensemble de l'app. Les tests reconstruisent leur propre engine via
les fixtures pytest (cf. tests/conftest.py).
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


def _normalize_url(url: str) -> str:
    """Railway et certains hébergeurs utilisent encore postgres:// (deprecated).
    SQLAlchemy 1.4+ exige postgresql://. On convertit silencieusement.
    """
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


def make_engine(database_url: str | None = None) -> Engine:
    """Crée un moteur SQLAlchemy.

    SQLite : options spécifiques pour multi-thread.
    Postgres : pool de taille raisonnable, pre-ping pour gérer les coupures réseau.
    """
    url = _normalize_url(database_url or get_settings().database_url)
    connect_args: dict[str, object] = {}
    engine_kwargs: dict[str, object] = {"future": True}

    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    else:
        # Postgres prod : pre_ping détecte les connexions mortes (Railway redémarre la DB
        # parfois) ; pool_recycle force le renouvellement après 30 min.
        engine_kwargs["pool_pre_ping"] = True
        engine_kwargs["pool_recycle"] = 1800

    return create_engine(url, connect_args=connect_args, **engine_kwargs)


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = make_engine()
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(),
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
            future=True,
        )
    return _SessionLocal


def get_db() -> Generator[Session, None, None]:
    """Dépendance FastAPI : renvoie une session DB scopée à la requête."""
    factory = get_session_factory()
    db = factory()
    try:
        yield db
    finally:
        db.close()
