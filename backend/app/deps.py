"""Dépendances FastAPI partagées (DB session, auth, services applicatifs)."""

from __future__ import annotations

from collections.abc import Generator

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session_factory


def get_db() -> Generator[Session, None, None]:
    factory = get_session_factory()
    db = factory()
    try:
        yield db
    finally:
        db.close()


def require_admin(authorization: str | None = Header(None)) -> bool:
    """Vérifie le token Bearer admin.

    Si ADMIN_API_TOKEN est vide (dev), aucune auth n'est requise.
    """
    expected = get_settings().admin_api_token
    if not expected:
        return True
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization[7:].strip()
    if token != expected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token"
        )
    return True


AdminAuthDep = Depends(require_admin)
DbDep = Depends(get_db)
