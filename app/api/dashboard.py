"""Section 6.4 — Instructor Dashboard endpoints, plus the provider-fallback
operations view (Section 4 of the build plan: log every provider used per call)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Assignment, Course, LLMCall, Student
from app.modules import dashboard, insights, profiler
from app.schemas import CourseCreate, VisibilityBody

def _subject_assignments(db: Session, subject_id: int | None) -> set[int] | None:
    """A subject's exercise ids, for the teacher's subject picker; None = all."""
    if subject_id is None:
        return None
    return set(db.scalars(select(Assignment.id).where(Assignment.subject_id == subject_id)).all())


router = APIRouter(tags=["instructor-dashboard"])


@router.get("/courses")
def list_courses(db: Session = Depends(get_db)):
    """Every course id the dashboard's course switcher should offer: ids
    explicitly registered via POST /courses, plus any id already in use by a
    student or assignment (the seeded CS101 has never had a Course row, only
    students/assignments — this is what still surfaces it here)."""
    ids = set(db.scalars(select(Course.course_id)).all())
    ids.update(db.scalars(select(Student.course_id).distinct()).all())
    ids.update(db.scalars(select(Assignment.course_id).distinct()).all())
    return sorted(ids)


@router.post("/courses", status_code=201)
def create_course(body: CourseCreate, db: Session = Depends(get_db)):
    """Registers a course id so it shows up in the switcher — and survives a
    reload — even before any student or exercise references it."""
    course_id = body.course_id.strip()
    already = (
        db.get(Course, course_id) is not None
        or db.scalar(select(Student.id).where(Student.course_id == course_id)) is not None
        or db.scalar(select(Assignment.id).where(Assignment.course_id == course_id)) is not None
    )
    if not already:
        db.add(Course(course_id=course_id))
        db.commit()
    return {"course_id": course_id, "created": not already}


@router.get("/courses/{course_id}/students/{student_id}/overview")
def student_overview(course_id: str, student_id: int, db: Session = Depends(get_db)):
    try:
        return dashboard.student_overview(db, course_id, student_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/courses/{course_id}/analytics/class-patterns")
def class_patterns(course_id: str, subject_id: int | None = None,
                   db: Session = Depends(get_db)):
    return dashboard.class_patterns(db, course_id, _subject_assignments(db, subject_id))


@router.get("/students/{student_id}/narrative-summary")
def narrative_summary(
    student_id: int,
    assignment_id: int | None = Query(None),
    db: Session = Depends(get_db),
):
    """On-demand only — a button click, never automatic. Dialogue tier.

    `assignment_id` scopes it to the assignment the instructor currently has
    selected; omitted, it falls back to whatever the student touched last
    across every assignment, which can blend two assignments together."""
    try:
        return dashboard.narrative_summary(db, student_id, assignment_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/students/{student_id}/dependency-profile")
def dependency_profile(
    student_id: int,
    viewer: str = Query("student", pattern="^(student|instructor)$"),
    db: Session = Depends(get_db),
):
    try:
        return profiler.profile(db, student_id, viewer=viewer)
    except profiler.ProfileNotVisible as exc:
        raise HTTPException(403, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/students/{student_id}/insights")
def student_insights(
    student_id: int,
    viewer: str = Query("student", pattern="^(student|instructor)$"),
    subject_id: int | None = None,
    db: Session = Depends(get_db),
):
    """Timing, strengths, weaknesses and the plan for one student.

    `viewer=student` is the default and returns everything EXCEPT the status
    tier. Which tier a student falls into is a conversation for their
    instructor to open, not a label to hand them in a panel — so it is left out
    of the payload rather than hidden in the page, and the student's own
    browser never receives it.
    """
    try:
        return insights.student_insights(db, student_id, viewer=viewer,
                                         assignment_ids=_subject_assignments(db, subject_id))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/courses/{course_id}/analytics/insights")
def class_insights(course_id: str, subject_id: int | None = None,
                   db: Session = Depends(get_db)):
    """The roster with a status tier beside every name, plus class averages.
    Instructor-facing by definition: it is a list of other people.
    `subject_id` limits every number to that subject's exercises."""
    return insights.class_insights(db, course_id, _subject_assignments(db, subject_id))


@router.put("/courses/{course_id}/settings/dependency-profile-visibility")
def set_visibility(course_id: str, body: VisibilityBody, db: Session = Depends(get_db)):
    return {"course_id": course_id,
            "visibility": profiler.set_visibility(db, course_id, body.visibility)}


@router.get("/ops/llm-calls")
def llm_calls(limit: int = Query(50, le=500), db: Session = Depends(get_db)):
    """Read this before the demo. If Groq is absorbing most of a burst, that is
    Mistral's free-tier RPM cap biting — expected, and exactly what the fallback
    is there for."""
    from app.main import flush_call_log

    flush_call_log(2.0)  # settle the background writer before reading

    by_provider = dict(db.execute(
        select(LLMCall.provider, func.count(LLMCall.id)).group_by(LLMCall.provider)
    ).all())
    successes = dict(db.execute(
        select(LLMCall.provider, func.count(LLMCall.id))
        .where(LLMCall.success.is_(True)).group_by(LLMCall.provider)
    ).all())
    latency = dict(db.execute(
        select(LLMCall.provider, func.avg(LLMCall.latency_ms))
        .where(LLMCall.success.is_(True)).group_by(LLMCall.provider)
    ).all())
    by_task = dict(db.execute(
        select(LLMCall.task, func.count(LLMCall.id)).group_by(LLMCall.task)
    ).all())

    total = sum(by_provider.values())
    fell_back = db.scalar(select(func.count(LLMCall.id)).where(LLMCall.fell_back.is_(True))) or 0

    recent = db.scalars(
        select(LLMCall).order_by(LLMCall.timestamp.desc(), LLMCall.id.desc()).limit(limit)
    ).all()

    from app.llm.client import breaker_status

    return {
        "total_calls": total,
        # Which providers are currently being skipped, and for how long.
        "breakers": breaker_status(),
        "calls_by_provider": by_provider,
        "successes_by_provider": successes,
        "mean_latency_ms_by_provider": {k: round(v or 0) for k, v in latency.items()},
        "calls_by_task": by_task,
        "fallback_calls": fell_back,
        "fallback_rate": round(fell_back / total, 3) if total else 0.0,
        "recent": [
            {
                "timestamp": row.timestamp.isoformat() if row.timestamp else None,
                "task": row.task, "tier": row.tier, "provider": row.provider,
                "model": row.model, "latency_ms": row.latency_ms,
                "success": row.success, "fell_back": row.fell_back,
                "attempts": row.attempts, "error_type": row.error_type,
            }
            for row in recent
        ],
    }
