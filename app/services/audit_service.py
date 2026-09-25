"""Audit history (spec §25) — append-only event log."""
from __future__ import annotations

from ..db import db
from ..models import AuditEvent


def record(actor_id: str | None, event_type: str, entity_type: str = "",
           entity_id: str = "", detail: dict | None = None) -> None:
    with db().session_scope() as session:
        session.add(AuditEvent(actor_id=actor_id, event_type=event_type,
                               entity_type=entity_type, entity_id=entity_id,
                               detail=detail or {}))


def history(entity_type: str, entity_id: str) -> list[dict]:
    with db().session_scope() as session:
        rows = session.query(AuditEvent).filter_by(
            entity_type=entity_type, entity_id=entity_id).order_by(
            AuditEvent.created_at).all()
        return [{"event": r.event_type, "actor": r.actor_id,
                 "detail": r.detail, "at": r.created_at.isoformat()} for r in rows]
