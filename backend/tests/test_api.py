"""Tests FastAPI : endpoints public + admin via TestClient.

Utilise un override sur get_db pour injecter une SQLite in-memory partagée
entre le test et l'app.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.deps import get_db
from app.main import create_app
from app.models import Base, Experiment, OrderRecord
from app.services.ledger import Ledger


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def engine():
    e = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # connexion unique partagée → tables visibles partout
    )
    Base.metadata.create_all(e)
    return e


@pytest.fixture
def session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


@pytest.fixture
def client(session_factory):
    app = create_app()

    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def seeded(session_factory):
    """Crée une expérience + un candidat Claude avec un ordre pending."""
    db = session_factory()
    try:
        exp = Experiment(
            name="Les Anges",
            started_at=datetime.now(timezone.utc),
            status="paper",
            duration_days=14,
        )
        db.add(exp)
        db.flush()
        ledger = Ledger(db)
        claude = ledger.create_candidate("Claude", "claude-opus-4-7", Decimal("10"))
        order = OrderRecord(
            candidate_id=claude.id,
            side="BUY",
            symbol="BTCUSDC",
            quote_amount_usdc=Decimal("8"),
            stop_loss_pct=Decimal("3"),
            take_profit_pct=Decimal("5"),
            status="pending_validation",
        )
        db.add(order)
        db.commit()
        return {
            "experiment_id": exp.id,
            "candidate_id": claude.id,
            "order_id": order.id,
        }
    finally:
        db.close()


# =============================================================================
# Health
# =============================================================================


def test_health_endpoint(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# =============================================================================
# Public endpoints
# =============================================================================


def test_get_experiment(client: TestClient, seeded) -> None:
    r = client.get("/api/experiment")
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "Les Anges"
    assert data["status"] == "paper"
    assert data["current_episode"] >= 1


def test_list_candidates(client: TestClient, seeded) -> None:
    r = client.get("/api/candidates")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["name"] == "Claude"
    assert Decimal(data[0]["wallet"]["usdc_balance"]) == Decimal("10")


def test_leaderboard(client: TestClient, seeded) -> None:
    r = client.get("/api/leaderboard")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["candidate_name"] == "Claude"
    assert Decimal(data[0]["portfolio_value_usdc"]) == Decimal("10")


def test_recent_orders(client: TestClient, seeded) -> None:
    r = client.get("/api/orders/recent")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["status"] == "pending_validation"


def test_costs_empty_then_aggregated(client: TestClient, session_factory, seeded) -> None:
    # Pas encore de decisions
    r = client.get("/api/costs")
    assert r.status_code == 200
    data = r.json()
    assert data["total_calls"] == 0
    assert Decimal(data["total_cost_usd"]) == Decimal("0")
    assert len(data["entries"]) == 1
    assert data["entries"][0]["candidate_name"] == "Claude"

    # On ajoute une decision avec des tokens
    from app.models import Decision

    db = session_factory()
    try:
        d = Decision(
            candidate_id=seeded["candidate_id"],
            episode_number=1,
            system_prompt="x",
            user_prompt="y",
            raw_response="z",
            parsed_action=None,
            provider="anthropic",
            model="claude-haiku-4-5-20251001",
            tokens_input=1000,
            tokens_output=500,
            latency_ms=10,
        )
        db.add(d)
        db.commit()
    finally:
        db.close()

    r = client.get("/api/costs")
    data = r.json()
    assert data["total_calls"] == 1
    # 1000 * 1/M + 500 * 5/M = 0.001 + 0.0025 = 0.0035 USD
    assert Decimal(data["total_cost_usd"]) == Decimal("0.0035000")
    assert data["entries"][0]["call_count"] == 1
    assert data["entries"][0]["provider"] == "anthropic"


def test_strategy_404_when_none(client: TestClient, seeded) -> None:
    r = client.get(f"/api/candidates/{seeded['candidate_id']}/strategy")
    assert r.status_code == 404


# =============================================================================
# Admin endpoints
# =============================================================================


def test_approve_order(client: TestClient, seeded) -> None:
    r = client.post(f"/admin/orders/{seeded['order_id']}/approve")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "approved"
    assert data["validated_at"] is not None


def test_reject_order_with_reason(client: TestClient, seeded) -> None:
    r = client.post(
        f"/admin/orders/{seeded['order_id']}/reject",
        json={"reason": "ordre trop risqué"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"
    assert r.json()["error_message"] == "ordre trop risqué"


def test_cannot_approve_twice(client: TestClient, seeded) -> None:
    r = client.post(f"/admin/orders/{seeded['order_id']}/approve")
    assert r.status_code == 200
    r2 = client.post(f"/admin/orders/{seeded['order_id']}/approve")
    assert r2.status_code == 409


def test_approve_unknown_order(client: TestClient, seeded) -> None:
    r = client.post("/admin/orders/99999/approve")
    assert r.status_code == 404


def test_kill_switch(client: TestClient, seeded) -> None:
    r = client.post("/admin/kill-switch")
    assert r.status_code == 200
    data = r.json()
    assert data["stopped"] is True
    assert data["pending_cancelled"] == 1


def test_create_candidate(client: TestClient) -> None:
    # Pas d'expérience préalable : on peut quand même créer un candidat
    r = client.post(
        "/admin/candidates",
        json={"name": "Mistral", "model": "mistral-large-3", "initial_balance_usdc": "10"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "Mistral"
    assert Decimal(data["wallet"]["usdc_balance"]) == Decimal("10")


def test_inject_episode_event(client: TestClient, seeded) -> None:
    r = client.post(
        "/admin/episode-events",
        json={
            "episode_number": 2,
            "kind": "competition_announce",
            "announcement_text": "Tu es maintenant en compétition.",
        },
    )
    assert r.status_code == 200


# =============================================================================
# Auth
# =============================================================================


def test_admin_requires_token_when_configured(session_factory, monkeypatch) -> None:
    """Quand admin_api_token est défini, les routes admin exigent le bearer."""
    monkeypatch.setenv("ADMIN_API_TOKEN", "secret-token")
    # Reset le cache lru de get_settings
    from app.config import get_settings
    get_settings.cache_clear()

    app = create_app()

    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        # sans token
        r = client.get("/admin/orders/pending")
        assert r.status_code == 401
        # mauvais token
        r = client.get(
            "/admin/orders/pending",
            headers={"Authorization": "Bearer wrong"},
        )
        assert r.status_code == 403
        # bon token
        r = client.get(
            "/admin/orders/pending",
            headers={"Authorization": "Bearer secret-token"},
        )
        assert r.status_code == 200

    get_settings.cache_clear()
