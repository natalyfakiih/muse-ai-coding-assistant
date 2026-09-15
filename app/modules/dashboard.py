"""Step 4 — Instructor Dashboard (Section 3.4).

Read-only analytics over data that already exists. No AI calls at read time:
the class-patterns view is a straightforward group-by, which is what keeps it
fast and reliable. The only LLM call in this module is the per-student narrative
summary, and it fires on a button click, never automatically.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.llm import DIALOGUE, generate_structured
from app.llm import prompts
from app.models import DiffResult, Interaction, Specification, Student
from app.modules import profiler
from app.modules.reflection import diff_public


# --------------------------------------------------------------------------
# Per-student view
# --------------------------------------------------------------------------
def student_overview(db: Session, course_id: str, student_id: int) -> dict:
    student = db.get(Student, student_id)
    if student is None or student.course_id != course_id:
        raise LookupError("No such student in this course.")

    specs = db.scalars(
        select(Specification)
        .where(Specification.student_id == student_id)
        .order_by(Specification.assignment_id, Specification.version)
    ).all()

    by_assignment: dict[int, list[Specification]] = defaultdict(list)
    for spec in specs:
        by_assignment[spec.assignment_id].append(spec)

    assignments = []
    for assignment_id, versions in by_assignment.items():
        latest = versions[-1]
        interactions = db.scalars(
            select(Interaction)
            .where(Interaction.specification_id == latest.id)
            .order_by(Interaction.timestamp, Interaction.id)
        ).all()
        assignments.append({
            "assignment_id": assignment_id,
            "assignment_prompt": latest.assignment.prompt_text,
            "specification_versions": [
                {
                    "id": v.id,
                    "version": v.version,
                    "status": v.status,
                    "created_at": v.created_at.isoformat() if v.created_at else None,
                    "content": v.content,
                }
                for v in versions
            ],
            "locked_specification_id": latest.id if latest.is_locked else None,
            "interactions": [_interaction_public(row) for row in interactions],
            "diff_results": [diff_public(d) for d in latest.diff_results],
            "submitted_at": latest.submitted_at.isoformat() if latest.submitted_at else None,
        })

    return {
        "student": {"id": student.id, "name": student.name,
                    "course_id": student.course_id, "cohort": student.cohort},
        "assignments": assignments,
    }


def _interaction_public(row: Interaction) -> dict:
    return {
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


# --------------------------------------------------------------------------
# Per-class view — pure SQL aggregation
# --------------------------------------------------------------------------
def class_patterns(db: Session, course_id: str) -> dict:
    total_students = db.scalar(
        select(func.count(Student.id)).where(Student.course_id == course_id)
    ) or 0

    # students touched by each (spec item, category) pair
    rows = db.execute(
        select(
            DiffResult.spec_item_key,
            DiffResult.category,
            func.count(func.distinct(Specification.student_id)),
            func.count(DiffResult.id),
        )
        .join(Specification, DiffResult.specification_id == Specification.id)
        .join(Student, Specification.student_id == Student.id)
        .where(Student.course_id == course_id)
        .group_by(DiffResult.spec_item_key, DiffResult.category)
        .order_by(func.count(func.distinct(Specification.student_id)).desc())
    ).all()

    patterns = [
        {
            "spec_item": key,
            "category": category,
            "students": students,
            "occurrences": occurrences,
            "share_of_class": round(students / total_students, 3) if total_students else 0.0,
        }
        for key, category, students, occurrences in rows
    ]

    category_totals = dict(db.execute(
        select(DiffResult.category, func.count(DiffResult.id))
        .join(Specification, DiffResult.specification_id == Specification.id)
        .join(Student, Specification.student_id == Student.id)
        .where(Student.course_id == course_id)
        .group_by(DiffResult.category)
    ).all())

    question_mix = dict(db.execute(
        select(Interaction.classified_type, func.count(Interaction.id))
        .join(Student, Interaction.student_id == Student.id)
        .where(Student.course_id == course_id)
        .group_by(Interaction.classified_type)
    ).all())

    mode_mix = dict(db.execute(
        select(Interaction.response_mode, func.count(Interaction.id))
        .join(Student, Interaction.student_id == Student.id)
        .where(Student.course_id == course_id)
        .group_by(Interaction.response_mode)
    ).all())

    spec_versions = db.execute(
        select(Specification.student_id, func.max(Specification.version))
        .join(Student, Specification.student_id == Student.id)
        .where(Student.course_id == course_id)
        .group_by(Specification.student_id)
    ).all()
    versions = [v for _, v in spec_versions]

    return {
        "course_id": course_id,
        "students": total_students,
        "diff_patterns": patterns,
        "diff_category_totals": category_totals,
        "question_mix": question_mix,
        "response_mode_mix": mode_mix,
        "escape_hatch_uses": mode_mix.get("hint", 0),
        "spec_versions_mean": round(sum(versions) / len(versions), 2) if versions else 0,
        "spec_versions_max": max(versions) if versions else 0,
    }


# --------------------------------------------------------------------------
# On-demand narrative summary — the one LLM call in this module
# --------------------------------------------------------------------------
def narrative_summary(db: Session, student_id: int) -> dict:
    student = db.get(Student, student_id)
    if student is None:
        raise LookupError("No such student.")

    specs = db.scalars(
        select(Specification).where(Specification.student_id == student_id)
        .order_by(Specification.version)
    ).all()
    if not specs:
        return {"student_id": student_id, "summary": "No specification work recorded yet."}

    latest = specs[-1]
    interactions = list(latest.interactions)
    counts = Counter(row.classified_type for row in interactions if row.classified_type)
    diff_counts = Counter(d.category for d in latest.diff_results)

    payload = json.dumps({
        "student_name": student.name,
        "assignment_prompt": latest.assignment.prompt_text,
        "specification_versions": len(specs),
        "first_specification": specs[0].content,
        "locked_specification": latest.content,
        "interaction_counts": dict(counts),
        "escape_hatch_uses": sum(1 for row in interactions if row.response_mode == "hint"),
        "diff_categories": dict(diff_counts),
        "diff_descriptions": [d.description for d in latest.diff_results],
        "student_explanations": [d.student_explanation for d in latest.diff_results
                                 if d.student_explanation],
    }, ensure_ascii=False)

    system, schema = prompts.NARRATIVE_SUMMARY
    raw = generate_structured(system, payload, schema, task="narrative_summary", tier=DIALOGUE)
    return {
        "student_id": student_id,
        "summary": (raw.get("summary") or "").strip() or "No summary generated.",
        "grounded_in": {
            "specification_versions": len(specs),
            "interactions": len(interactions),
            "diff_results": len(latest.diff_results),
        },
    }


# --------------------------------------------------------------------------
def dependency_profile(db: Session, student_id: int, *, viewer: str = "student") -> dict:
    return profiler.profile(db, student_id, viewer=viewer)
