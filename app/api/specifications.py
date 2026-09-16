"""Section 6.1 — Specification Gate endpoints."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Assignment, Specification, Student
from app.modules import spec_gate
from app.schemas import AssignmentCreate, SpecLock, SpecMessage

router = APIRouter(tags=["specification-gate"])


def _assignment(db: Session, assignment_id: int) -> Assignment:
    assignment = db.get(Assignment, assignment_id)
    if assignment is None:
        raise HTTPException(404, "No such assignment.")
    return assignment


@router.post("/assignments/{assignment_id}/specification")
def continue_dialogue(assignment_id: int, body: SpecMessage, db: Session = Depends(get_db)):
    """Start or continue the clarification dialogue; body = student message."""
    _assignment(db, assignment_id)
    if db.get(Student, body.student_id) is None:
        raise HTTPException(404, "No such student.")
    spec = spec_gate.start_specification(db, assignment_id, body.student_id)
    return spec_gate.advance(db, spec, body.message)


@router.get("/assignments/{assignment_id}/specification")
def read_specification(
    assignment_id: int,
    student_id: int = Query(...),
    db: Session = Depends(get_db),
):
    """Current Specification JSON + lock status."""
    assignment = _assignment(db, assignment_id)
    spec = spec_gate.current_specification(db, assignment_id, student_id)
    if spec is None:
        return {
            "specification_id": None,
            "version": 0,
            "status": "not_started",
            "locked": False,
            "content": dict(spec_gate.EMPTY_SPEC),
            "assignment": _assignment_public(assignment),
            "rubric": assignment.rubric_items,
        }
    history = [
        {"id": v.id, "version": v.version, "status": v.status,
         "content": v.content,
         "created_at": v.created_at.isoformat() if v.created_at else None}
        for v in db.query(Specification)
        .filter(Specification.assignment_id == assignment_id,
                Specification.student_id == student_id)
        .order_by(Specification.version)
        .all()
    ]
    return {
        "specification_id": spec.id,
        "version": spec.version,
        "status": spec.status,
        "locked": spec.is_locked,
        "content": spec.content,
        "gap_ask_counts": spec.gap_ask_counts,
        "confirmed_gaps": spec.confirmed_gaps,
        "assignment": _assignment_public(assignment),
        "rubric": assignment.rubric_items,
        "version_history": history,
        "progress": spec_gate.completeness(spec.content or {}, assignment.rubric_items),
        "rubric_status": spec_gate.rubric_status(
            spec.content or {}, assignment.rubric_items, spec),
        "dont_know_counts": spec.dont_know_counts,
    }


@router.post("/assignments/{assignment_id}/specification/lock")
def force_lock(assignment_id: int, body: SpecLock, db: Session = Depends(get_db)):
    """Force-lock: the student confirms their answer stands after the 2nd ask."""
    _assignment(db, assignment_id)
    spec = spec_gate.current_specification(db, assignment_id, body.student_id)
    if spec is None:
        raise HTTPException(404, "No specification started for this student.")
    return spec_gate.force_lock(db, spec, body.gap_id)


@router.post("/assignments/{assignment_id}/specification/dont-know")
def dont_know(assignment_id: int, body: SpecLock, db: Session = Depends(get_db)):
    """Student doesn't know how to answer the current question. Explains what is
    being asked — and, for a real design decision, the legitimate options —
    without answering it for them or skipping the item."""
    _assignment(db, assignment_id)
    spec = spec_gate.current_specification(db, assignment_id, body.student_id)
    if spec is None:
        raise HTTPException(404, "No specification started for this student.")
    return spec_gate.dont_know(db, spec)


@router.get("/assignments")
def list_assignments(course_id: str | None = None, db: Session = Depends(get_db)):
    query = db.query(Assignment)
    if course_id:
        query = query.filter(Assignment.course_id == course_id)
    return [_assignment_public(a) for a in query.order_by(Assignment.id).all()]


def _slugify_item_id(text: str, used: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "item"
    candidate, n = base, 2
    while candidate in used:
        candidate = f"{base}_{n}"
        n += 1
    used.add(candidate)
    return candidate


@router.post("/assignments", status_code=201)
def create_assignment(body: AssignmentCreate, db: Session = Depends(get_db)):
    """Instructor-authored exercise — the counterpart to the seed data in
    app/seed.py, but reachable from the dashboard instead of a script.

    Rubric items are stored without an explicit "field": spec_gate's
    required_field() infers goal/inputs/outputs/examples from the wording
    itself, so the instructor only ever writes plain-language checklist
    lines, never internal field names.
    """
    used_ids: set[str] = set()
    items = []
    for index, item in enumerate(body.items):
        explicit = (item.id or "").strip()
        item_id = explicit or _slugify_item_id(item.text, used_ids)
        if explicit:
            used_ids.add(item_id)
        items.append({"id": item_id, "priority": index + 1, "text": item.text.strip()})

    assignment = Assignment(
        course_id=body.course_id.strip(),
        prompt_text=body.prompt_text.strip(),
        starter_code=(body.starter_code or None),
        rubric={"entry_point": body.entry_point.strip(), "items": items},
    )
    db.add(assignment)
    db.commit()
    db.refresh(assignment)
    return _assignment_public(assignment)


@router.get("/students")
def list_students(course_id: str | None = None, db: Session = Depends(get_db)):
    query = db.query(Student)
    if course_id:
        query = query.filter(Student.course_id == course_id)
    return [
        {"id": s.id, "name": s.name, "course_id": s.course_id, "cohort": s.cohort}
        for s in query.order_by(Student.id).all()
    ]


def _assignment_public(assignment: Assignment) -> dict:
    return {
        "id": assignment.id,
        "course_id": assignment.course_id,
        "prompt_text": assignment.prompt_text,
        "starter_code": assignment.starter_code,
        "entry_point": assignment.entry_point,
        "rubric_items": assignment.rubric_items,
    }
