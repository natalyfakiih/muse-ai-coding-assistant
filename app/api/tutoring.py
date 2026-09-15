"""Section 6.2 — Tutor Layer endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import Interaction, Specification
from app.modules import tutor
from app.schemas import EscapeHatchRequest, SnapshotBody, TutorMessage

router = APIRouter(tags=["tutor-layer"])


def _spec(db: Session, specification_id: int) -> Specification:
    spec = db.get(Specification, specification_id)
    if spec is None:
        raise HTTPException(404, "No such specification.")
    return spec


@router.post("/specifications/{specification_id}/interactions")
def ask(specification_id: int, body: TutorMessage, db: Session = Depends(get_db)):
    """Student message + current code diff -> classification + response."""
    spec = _spec(db, specification_id)
    if not spec.is_locked:
        raise HTTPException(409, "The specification is not locked yet — the tutor is unavailable.")
    return tutor.handle_message(db, spec, body.message, body.code, body.code_diff)


@router.get("/specifications/{specification_id}/interactions")
def history(specification_id: int, db: Session = Depends(get_db)):
    spec = _spec(db, specification_id)
    rows = db.scalars(
        select(Interaction)
        .where(Interaction.specification_id == spec.id)
        .order_by(Interaction.timestamp, Interaction.id)
    ).all()
    return {
        "specification_id": spec.id,
        "interactions": [
            {
                "id": row.id,
                "timestamp": row.timestamp.isoformat() if row.timestamp else None,
                "student_message": row.student_message,
                "classified_type": row.classified_type,
                "confidence": row.confidence,
                "reasoning": row.reasoning,
                "ai_response": row.ai_response,
                "response_mode": row.response_mode,
                "thread_round": row.thread_round,
                "assist_cost": row.assist_cost,
            }
            for row in rows
        ],
        "open_thread_rounds": len(tutor.open_thread(db, spec.id)),
        "escape_hatch_available": tutor.escape_hatch_available(db, spec.id),
        "escape_hatch_threshold": settings.TUTOR_ESCAPE_HATCH_ROUNDS,
    }


@router.post("/specifications/{specification_id}/interactions/escape-hatch")
def escape_hatch(specification_id: int, body: EscapeHatchRequest, db: Session = Depends(get_db)):
    """Direct hint after N unresolved rounds; logs an assist cost."""
    spec = _spec(db, specification_id)
    return tutor.use_escape_hatch(db, spec, body.message)


@router.post("/specifications/{specification_id}/snapshots")
def autosave(specification_id: int, body: SnapshotBody, db: Session = Depends(get_db)):
    """Periodic editor autosave. Not in the spec's endpoint tables, but the
    CodeSnapshot entity needs a writer other than submission."""
    spec = _spec(db, specification_id)
    previous = tutor.latest_snapshot(db, spec.id)
    if previous is not None and previous.content == body.code and not body.is_final:
        return {"snapshot_id": previous.id, "saved": False}
    snapshot = tutor.save_snapshot(db, spec.id, body.code, body.is_final)
    db.commit()
    return {"snapshot_id": snapshot.id, "saved": True}


@router.get("/specifications/{specification_id}/snapshots/latest")
def latest_snapshot(specification_id: int, db: Session = Depends(get_db)):
    spec = _spec(db, specification_id)
    snapshot = tutor.latest_snapshot(db, spec.id)
    if snapshot is None:
        return {"snapshot_id": None, "content": spec.assignment.starter_code or ""}
    return {
        "snapshot_id": snapshot.id,
        "content": snapshot.content,
        "timestamp": snapshot.timestamp.isoformat() if snapshot.timestamp else None,
        "is_final": snapshot.is_final,
    }
