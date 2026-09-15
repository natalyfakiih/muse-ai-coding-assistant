"""Section 6.3 — Reflection Engine endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import DiffResult, Specification
from app.modules import reflection
from app.schemas import ExplanationBody, FlagBody, SubmitBody

router = APIRouter(tags=["reflection-engine"])


def _spec(db: Session, specification_id: int) -> Specification:
    spec = db.get(Specification, specification_id)
    if spec is None:
        raise HTTPException(404, "No such specification.")
    return spec


@router.post("/specifications/{specification_id}/submit")
def submit(specification_id: int, body: SubmitBody, db: Session = Depends(get_db)):
    """Runs the behavioral diff + classification; returns DiffResults needing
    an explanation."""
    return reflection.submit(db, _spec(db, specification_id), body.code)


@router.post("/diff-results/{diff_result_id}/explanation")
def explain(diff_result_id: int, body: ExplanationBody, db: Session = Depends(get_db)):
    diff = db.get(DiffResult, diff_result_id)
    if diff is None:
        raise HTTPException(404, "No such diff result.")
    return reflection.record_explanation(db, diff, body.explanation)


@router.post("/diff-results/{diff_result_id}/discuss")
def discuss(diff_result_id: int, body: ExplanationBody, db: Session = Depends(get_db)):
    """Work through one discrepancy with the tutor. Always a counter-question —
    reflection probes, it never fixes, and the explanation is still the
    student's to write."""
    diff = db.get(DiffResult, diff_result_id)
    if diff is None:
        raise HTTPException(404, "No such diff result.")
    return reflection.discuss(db, diff, body.explanation)


@router.get("/diff-results/{diff_result_id}/thread")
def thread(diff_result_id: int, db: Session = Depends(get_db)):
    if db.get(DiffResult, diff_result_id) is None:
        raise HTTPException(404, "No such diff result.")
    return {"diff_result_id": diff_result_id,
            "thread": reflection.diagnosis_thread(db, diff_result_id)}


@router.get("/specifications/{specification_id}/submission-status")
def status(specification_id: int, db: Session = Depends(get_db)):
    return reflection.submission_status(db, _spec(db, specification_id))


@router.post("/specifications/{specification_id}/finalize")
def finalize(specification_id: int, db: Session = Depends(get_db)):
    """Accept the submission. Refuses while any explanation is missing — the
    mandatory reflection step is enforced here, not in the UI."""
    return reflection.finalize(db, _spec(db, specification_id))


@router.post("/diff-results/{diff_result_id}/flag")
def flag(diff_result_id: int, body: FlagBody, db: Session = Depends(get_db)):
    diff = db.get(DiffResult, diff_result_id)
    if diff is None:
        raise HTTPException(404, "No such diff result.")
    diff.instructor_flag = body.instructor_flag
    db.commit()
    return reflection.diff_public(diff)
