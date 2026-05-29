"""Fixtures pytest pour les tests.

Une DB SQLite en mémoire est créée pour chaque test, garantissant l'isolation.
"""

from __future__ import annotations

from collections.abc import Generator
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.ledger_models import Base, Candidate
from app.services.ledger import Ledger


@pytest.fixture
def engine() -> Engine:
    """Moteur SQLite en mémoire avec schéma créé."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def db(engine: Engine) -> Generator[Session, None, None]:
    """Session DB pour un test, rollback à la fin."""
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def ledger(db: Session) -> Ledger:
    return Ledger(db)


@pytest.fixture
def claude(ledger: Ledger) -> Candidate:
    """Candidat Claude prêt à trader avec 10 USDC."""
    return ledger.create_candidate(
        name="Claude", model="claude-opus-4-7", initial_balance_usdc=Decimal("10")
    )
