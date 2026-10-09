"""Subjects, the material an instructor teaches from, and Ollie explaining it.

Writing or pasting material is the only path here on purpose. A file upload is
a parser, not a design: it would extract text and write it into the same `body`
field these endpoints already take, so it can sit on top of this later without
anything below it changing.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Subject, SubjectMaterial
from app.modules import material as material_module
from app.schemas import MaterialCreate, QuestionBody, SubjectCreate

router = APIRouter(tags=["subjects"])

CHECKERS = {
    "python": "Runs the student's code and diffs its behaviour against their specification.",
    "math": "Compares the student's declared answer with the expected one, symbolically.",
}


def _subject(db: Session, subject_id: int) -> Subject:
    subject = db.get(Subject, subject_id)
    if subject is None:
        raise HTTPException(404, "No such subject.")
    return subject


def _public(subject: Subject, materials: int = 0) -> dict:
    return {
        "id": subject.id,
        "course_id": subject.course_id,
        "name": subject.name,
        "checker": subject.checker,
        "checker_description": CHECKERS.get(subject.checker, ""),
        "description": subject.description,
        "materials": materials,
    }


@router.get("/checkers")
def list_checkers():
    """What a new subject can be marked by. The honest list, so an instructor
    picking "math" knows what phase 3 will actually do."""
    return [{"id": key, "description": text} for key, text in CHECKERS.items()]


@router.get("/courses/{course_id}/subjects")
def list_subjects(course_id: str, db: Session = Depends(get_db)):
    subjects = db.scalars(
        select(Subject).where(Subject.course_id == course_id).order_by(Subject.id)
    ).all()
    return [_public(s, len(s.materials)) for s in subjects]


@router.post("/courses/{course_id}/subjects", status_code=201)
def create_subject(course_id: str, body: SubjectCreate, db: Session = Depends(get_db)):
    if body.checker not in CHECKERS:
        raise HTTPException(400, f"Unknown checker {body.checker!r}. "
                                 f"One of: {', '.join(CHECKERS)}.")
    subject = Subject(course_id=course_id, name=body.name.strip(),
                      checker=body.checker, description=(body.description or "").strip() or None)
    db.add(subject)
    db.commit()
    db.refresh(subject)
    return _public(subject)


@router.get("/subjects/{subject_id}/materials")
def list_materials(subject_id: int, db: Session = Depends(get_db)):
    subject = _subject(db, subject_id)
    # `words` is here so the Learn page can show how long a note takes to read
    # before the student commits to it. Counted server-side because the list is
    # also what the instructor page shows, and the two should agree.
    return [{"id": m.id, "title": m.title, "body": m.body, "position": m.position,
             "words": len((m.body or "").split())}
            for m in subject.materials]


@router.post("/subjects/{subject_id}/materials", status_code=201)
def add_material(subject_id: int, body: MaterialCreate, db: Session = Depends(get_db)):
    subject = _subject(db, subject_id)
    row = SubjectMaterial(
        subject_id=subject.id, title=body.title.strip(), body=body.body,
        position=body.position if body.position is not None else len(subject.materials),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "title": row.title, "position": row.position,
            "paragraphs": len(material_module.chunks_for(db, subject.id))}


@router.post("/subjects/{subject_id}/explain")
def explain(subject_id: int, body: QuestionBody, db: Session = Depends(get_db)):
    """Ollie, on the instructor's material only.

    When nothing in the material is relevant this returns `covered: false`
    without calling a model at all — the refusal is decided in Python, so there
    is no prompt for a model to be persuaded out of.
    """
    try:
        return material_module.explain(db, subject_id, body.question)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/subjects/{subject_id}/materials/{material_id}/teach")
def teach(subject_id: int, material_id: int, db: Session = Depends(get_db)):
    """Ollie teaching one note, for the Learn side of the app.

    POST rather than GET even though it reads nothing: it spends a model call,
    so it must not be something a browser or a crawler can retry for free.
    """
    try:
        return material_module.teach(db, subject_id, material_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
