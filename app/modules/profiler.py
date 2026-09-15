"""Section 3.5 — AI Dependency Profiling (optional module).

Analytics over the interaction log, not a new data-producing component. No LLM
calls: every signal here is arithmetic over rows that already exist.

Visibility: this defaults to student-facing. Instructor visibility is a
per-course opt-in stored in `course_settings`, not a UI toggle — if a course
has not opted in, an instructor viewer gets a refusal from this function, not a
hidden div in the dashboard.
"""
from __future__ import annotations

import re
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CourseSettings, Interaction, Specification, Student

STOPWORDS = frozenset("""
a an and are as at be but by can do does for from how i if in is it my not of on or so that the
this to was what when where which who why with you your me am get got need help please
""".split())


def visibility(db: Session, course_id: str) -> str:
    row = db.get(CourseSettings, course_id)
    return row.dependency_profile_visibility if row else "student_only"


def set_visibility(db: Session, course_id: str, value: str) -> str:
    if value not in ("student_only", "instructor_opt_in"):
        raise ValueError("visibility must be 'student_only' or 'instructor_opt_in'")
    row = db.get(CourseSettings, course_id)
    if row is None:
        row = CourseSettings(course_id=course_id, dependency_profile_visibility=value)
        db.add(row)
    else:
        row.dependency_profile_visibility = value
    db.commit()
    return value


class ProfileNotVisible(PermissionError):
    pass


def profile(db: Session, student_id: int, *, viewer: str = "student") -> dict:
    student = db.get(Student, student_id)
    if student is None:
        raise LookupError("No such student.")

    if viewer == "instructor" and visibility(db, student.course_id) != "instructor_opt_in":
        raise ProfileNotVisible(
            "This course has not opted in to instructor visibility of dependency profiles."
        )

    specs = db.scalars(
        select(Specification).where(Specification.student_id == student_id)
    ).all()
    spec_ids = [s.id for s in specs]
    interactions = (
        db.scalars(
            select(Interaction)
            .where(Interaction.specification_id.in_(spec_ids))
            .order_by(Interaction.timestamp, Interaction.id)
        ).all()
        if spec_ids else []
    )

    counts = Counter(row.classified_type for row in interactions if row.classified_type)
    problem_solving = counts.get("problem_solving", 0)
    factual = counts.get("factual", 0)

    return {
        "student_id": student_id,
        "viewer": viewer,
        "visibility": visibility(db, student.course_id),
        "total_questions": len(interactions),
        "problem_solving": problem_solving,
        "factual": factual,
        "problem_solving_ratio": round(problem_solving / len(interactions), 3) if interactions else 0.0,
        "repeated_topics": _repeated_topics(interactions),
        "escape_hatch_uses": sum(1 for row in interactions if row.response_mode == "hint"),
        "assist_cost_total": sum(row.assist_cost for row in interactions),
        "longest_unresolved_thread": max((row.thread_round for row in interactions), default=0),
        "time_to_first_message_seconds": _time_to_first_message(specs, interactions),
        "specification_versions": max((s.version for s in specs), default=0),
        "note": (
            "This profile is a self-reflection tool. It is not part of any grade."
        ),
    }


def _repeated_topics(interactions: list[Interaction], min_repeats: int = 2) -> list[dict]:
    """Cheap keyword overlap — a student circling the same word across several
    questions is the signal worth surfacing, not exact duplicates."""
    counter: Counter[str] = Counter()
    for row in interactions:
        words = {
            w for w in re.findall(r"[a-zA-Z_]{4,}", (row.student_message or "").lower())
            if w not in STOPWORDS
        }
        counter.update(words)
    return [
        {"topic": word, "questions": count}
        for word, count in counter.most_common(8)
        if count >= min_repeats
    ]


def _time_to_first_message(specs, interactions) -> int | None:
    """Seconds between opening the assignment (first spec row) and the first
    tutor message. A very short gap is worth a student noticing about
    themselves."""
    if not specs or not interactions:
        return None
    opened = min(s.created_at for s in specs if s.created_at)
    first = interactions[0].timestamp
    if not opened or not first:
        return None
    return max(0, int((first - opened).total_seconds()))
