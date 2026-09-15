"""Session statistics for the Reflection phase.

Everything here is arithmetic over rows that already exist — no LLM calls, so
the panel is instant and costs nothing. The point is for a student to see the
shape of their own session: how many times they were asked again before an
answer stuck, how much they leaned on the tutor, how many of their own declared
cases their code actually passed.

This is the same material the Dependency Profile is built from, presented per
assignment rather than per student.
"""
from __future__ import annotations

from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    CodeSnapshot,
    DiffResult,
    GateTurn,
    Interaction,
    Specification,
)


def _seconds_between(start, end) -> int | None:
    if not start or not end:
        return None
    return max(0, int((end - start).total_seconds()))


def session_stats(db: Session, spec: Specification) -> dict:
    versions = db.scalars(
        select(Specification)
        .where(
            Specification.assignment_id == spec.assignment_id,
            Specification.student_id == spec.student_id,
        )
        .order_by(Specification.version)
    ).all()

    turns = db.scalars(
        select(GateTurn)
        .where(
            GateTurn.assignment_id == spec.assignment_id,
            GateTurn.student_id == spec.student_id,
        )
        .order_by(GateTurn.timestamp, GateTurn.id)
    ).all()

    interactions = db.scalars(
        select(Interaction)
        .where(Interaction.specification_id == spec.id)
        .order_by(Interaction.timestamp, Interaction.id)
    ).all()

    snapshots = db.scalars(
        select(CodeSnapshot)
        .where(CodeSnapshot.specification_id == spec.id)
        .order_by(CodeSnapshot.timestamp, CodeSnapshot.id)
    ).all()

    diffs = db.scalars(
        select(DiffResult).where(DiffResult.specification_id == spec.id)
    ).all()

    locked = next((v for v in versions if v.is_locked), None)
    asks = locked.gap_ask_counts if locked else (spec.gap_ask_counts or {})
    dont_knows = locked.dont_know_counts if locked else (spec.dont_know_counts or {})

    # A gap asked more than once means an answer that didn't settle it. That
    # "asked again" count is the honest measure of retries, not the raw ask total.
    re_asks = sum(max(0, count - 1) for count in (asks or {}).values())

    tutor = [row for row in interactions if row.response_mode != "diagnostic"]
    diagnosis = [row for row in interactions if row.response_mode == "diagnostic"]
    kinds = Counter(row.classified_type for row in tutor if row.classified_type)

    submissions = [s for s in snapshots if s.is_final]
    final_case_rows = [d.test_detail or {} for d in diffs]

    phase1 = {
        "versions": len(versions),
        "questions_asked": sum((asks or {}).values()),
        "asked_again": re_asks,
        "dont_know_presses": sum((dont_knows or {}).values()),
        "answers_on_record": len((locked or spec).confirmed_gaps or []),
        "seconds": _seconds_between(
            versions[0].created_at if versions else None,
            locked.created_at if locked else None,
        ),
    }

    phase2 = {
        "questions_asked": len(tutor),
        "factual": kinds.get("factual", 0),
        "problem_solving": kinds.get("problem_solving", 0),
        "counter_questions": sum(1 for r in tutor if r.response_mode == "counter_question"),
        "direct_hints": sum(1 for r in tutor if r.response_mode == "hint"),
        "assist_cost": sum(r.assist_cost for r in tutor),
        "longest_thread": max((r.thread_round for r in tutor), default=0),
        "code_saves": len(snapshots),
        "seconds": _seconds_between(
            snapshots[0].timestamp if snapshots else None,
            submissions[-1].timestamp if submissions else None,
        ),
    }

    phase3 = {
        "submissions": len(submissions),
        "cases_failed": len(diffs),
        "discrepancies": len(diffs),
        "by_category": dict(Counter(d.category for d in diffs)),
        "diagnosis_rounds": len(diagnosis),
        "explanations_written": sum(1 for d in diffs if (d.student_explanation or "").strip()),
        "failed_cases": [
            {
                "source": row.get("source"),
                "input": row.get("input"),
                "expected": row.get("expected"),
                "actual": row.get("actual"),
                "failure": row.get("failure"),
            }
            for row in final_case_rows
        ],
    }

    return {
        "specification_id": spec.id,
        "student": spec.student.name if spec.student else None,
        "phase1": phase1,
        "phase2": phase2,
        "phase3": phase3,
        "totals": {
            "ai_questions": phase1["questions_asked"] + phase2["counter_questions"]
            + phase3["diagnosis_rounds"],
            "student_messages": len([t for t in turns if t.student_message])
            + len(interactions),
        },
        "transcript": transcript(turns, interactions, diffs),
    }


def transcript(turns, interactions, diffs) -> list[dict]:
    """Every question and the answer that followed it, in order, tagged by phase.

    Gate turns and tutor interactions live in different tables for good reasons
    (see GateTurn's docstring), so they're merged here rather than in the UI.
    """
    entries = []

    for turn in turns:
        entries.append({
            "phase": 1,
            "at": turn.timestamp.isoformat() if turn.timestamp else None,
            "student": turn.student_message,
            "muse": turn.question,
            "tag": turn.kind,
            "about": turn.gap_id,
            "note": turn.shortfall,
        })

    by_diff = {d.id: d for d in diffs}
    for row in interactions:
        diff = by_diff.get(row.diff_result_id) if row.diff_result_id else None
        entries.append({
            "phase": 3 if row.response_mode == "diagnostic" else 2,
            "at": row.timestamp.isoformat() if row.timestamp else None,
            "student": row.student_message,
            "muse": row.ai_response,
            "tag": row.response_mode,
            "about": (diff.category if diff else row.classified_type),
            "note": None,
        })

    entries.sort(key=lambda e: e["at"] or "")
    return entries
