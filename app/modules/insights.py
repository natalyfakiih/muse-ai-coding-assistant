"""Per-student and per-class insight, computed in Python.

Three things live here:

* **Timing.** How long the specification took, how long the coding took, how
  long between retries, and how each of those compares to the class.
* **A status tier** — on track / needs attention / at risk — built from signals
  that each carry their own number and the rows that produced it. The tier is
  never the output on its own: a label an instructor cannot interrogate is worse
  than no label, because they will act on it anyway.
* **Strengths, weaknesses and a plan**, derived from which rubric areas the
  student struggled with and which they did not.

No LLM calls, by design. A judgement attached to a student's name has to be
reproducible, auditable and the same on Tuesday as it was on Monday. Everything
below is counting and division over rows that already exist.

Visibility: `viewer="student"` returns the student's own numbers, charts and
plan, but NOT the tier. Telling someone they are "at risk" tends to produce the
thing it names; the instructor gets the label, the student gets the plan.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from statistics import mean, median

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    CodeSnapshot,
    DiffResult,
    GateTurn,
    Interaction,
    Specification,
    Student,
    SubmissionAttempt,
)

# --------------------------------------------------------------------------
# Tunables. Exposed as module constants so a course can be calibrated without
# touching the logic, and so the tests can state the thresholds they assume.
# --------------------------------------------------------------------------
AT_RISK_SCORE = 5          # total signal points at or above which: at risk
NEEDS_ATTENTION_SCORE = 2  # ... and above which: needs attention

HIGH_DRIFT_SHARE = 0.60    # most differences are unnoticed drift
HIGH_FACTUAL_SHARE = 0.60  # mostly asking what Python does, not why theirs fails
SLOW_FACTOR = 1.75         # this much slower than the class median
MANY_RETRIES = 3           # submissions before the differences cleared
MANY_HINTS = 3

# "not_started" is a separate state, not the best tier. A student who has done
# nothing scores zero on every signal, and reporting that as "On track" is the
# most misleading thing this module could say.
TIERS = ("on_track", "needs_attention", "at_risk", "not_started")
TIER_LABELS = {
    "on_track": "On track",
    "needs_attention": "Needs attention",
    "at_risk": "At risk",
    "not_started": "Not started",
}


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; Postgres hands back aware ones."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _span(start: datetime | None, end: datetime | None) -> int | None:
    start, end = _as_utc(start), _as_utc(end)
    if start is None or end is None:
        return None
    return max(0, int((end - start).total_seconds()))


def _ratio(part: int, whole: int) -> float:
    return round(part / whole, 3) if whole else 0.0


# --------------------------------------------------------------------------
# Timing, per attempt
# --------------------------------------------------------------------------
def attempt_timing(db: Session, student_id: int, assignment_id: int,
                   specs: list[Specification]) -> dict:
    """How one student spent their time on one assignment.

    Phases are bounded by rows that already exist: the first gate turn opens the
    specification phase, the locked version closes it, the first code snapshot
    opens the coding phase, the final submission closes it.
    """
    spec_ids = [s.id for s in specs]
    latest = specs[-1]

    turns = db.scalars(
        select(GateTurn)
        .where(GateTurn.student_id == student_id, GateTurn.assignment_id == assignment_id)
        .order_by(GateTurn.timestamp)
    ).all()
    snapshots = db.scalars(
        select(CodeSnapshot)
        .where(CodeSnapshot.specification_id.in_(spec_ids))
        .order_by(CodeSnapshot.timestamp)
    ).all() if spec_ids else []
    attempts = db.scalars(
        select(SubmissionAttempt)
        .where(SubmissionAttempt.specification_id.in_(spec_ids))
        .order_by(SubmissionAttempt.attempt_number)
    ).all() if spec_ids else []

    opened = _as_utc(specs[0].created_at)
    spec_done = _as_utc(latest.created_at) if latest.is_locked else None
    coding_from = _as_utc(snapshots[0].timestamp) if snapshots else None
    finished = _as_utc(latest.submitted_at)

    gaps = [a.seconds_since_previous for a in attempts if a.seconds_since_previous is not None]
    cleared = next((a for a in attempts if a.differences == 0), None)

    return {
        "assignment_id": assignment_id,
        "assignment": latest.assignment.prompt_text[:90] if latest.assignment else "",
        "entry_point": (latest.assignment.rubric or {}).get("entry_point") if latest.assignment else None,
        "specification_seconds": _span(opened, spec_done),
        "coding_seconds": _span(coding_from, finished),
        "total_seconds": _span(opened, finished or (snapshots[-1].timestamp if snapshots else None)),
        "versions": len(specs),
        "gate_turns": len(turns),
        "submissions": len(attempts),
        "retry_gaps_seconds": gaps,
        "mean_retry_gap_seconds": int(mean(gaps)) if gaps else None,
        "solved": cleared is not None,
        "submissions_to_solve": cleared.attempt_number if cleared else None,
        "locked": latest.is_locked,
        "submitted": latest.submitted_at is not None,
    }


# --------------------------------------------------------------------------
# Areas: where a student is strong and where they are not
# --------------------------------------------------------------------------
AREAS = {
    "specification": "Writing the specification",
    "edge_cases": "Boundary and edge cases",
    "types": "Return types and shapes",
    "examples": "Concrete examples",
    "debugging": "Debugging their own code",
    "reflection": "Explaining their own work",
    "independence": "Working without hints",
}

# Which area a failed rubric item or a discrepancy belongs to. Matched against
# the spec_item_key, which is either a field name or "edge_case:<prose>".
_AREA_KEYS = (
    ("edge_case", "edge_cases"),
    ("empty", "edge_cases"),
    ("negative", "edge_cases"),
    ("zero", "edge_cases"),
    ("outputs", "types"),
    ("inputs", "types"),
    ("examples", "examples"),
    ("goal", "specification"),
    ("order", "specification"),
)


def area_for(key: str | None) -> str | None:
    text = str(key or "").lower()
    for needle, area in _AREA_KEYS:
        if needle in text:
            return area
    return None


def _area_scores(diffs: list[DiffResult], interactions: list[Interaction],
                 timings: list[dict], gap_asks: Counter) -> dict[str, dict]:
    """Score every area from 0 (struggling) to 1 (solid), with its evidence.

    A score is only reported when there is something to base it on. An area with
    no evidence is left out entirely rather than defaulting to the middle, which
    would quietly invent a judgement.
    """
    scores: dict[str, dict] = {}

    # Differences per area. More differences in an area means a weaker area, and
    # drift counts double: a bug is a mistake, drift is not having noticed.
    weight = Counter()
    evidence: dict[str, list[str]] = {}
    for diff in diffs:
        area = area_for(diff.spec_item_key)
        if area is None:
            continue
        weight[area] += 2 if diff.category == "drift" else 1
        evidence.setdefault(area, []).append(
            f"{diff.category}: {(diff.spec_item_key or '')[:60]}")

    # Re-asks during the gate: an area the student was asked about repeatedly.
    for gap_id, count in gap_asks.items():
        area = area_for(gap_id)
        if area and count > 1:
            weight[area] += count - 1
            evidence.setdefault(area, []).append(f"asked {count}× about {gap_id} in the gate")

    graded = set(weight) | {a for a in ("specification", "edge_cases", "types", "examples")
                            if diffs or gap_asks}
    for area in graded:
        hits = weight.get(area, 0)
        scores[area] = {
            "score": round(max(0.0, 1.0 - hits / 6), 2),
            "evidence": evidence.get(area, ["no differences recorded in this area"]),
            "hits": hits,
        }

    # Debugging: of the questions about their own code, how many did they
    # resolve without taking a hint.
    own_code = [i for i in interactions if i.classified_type == "problem_solving"]
    hints = [i for i in interactions if i.response_mode == "hint"]
    if interactions:
        scores["debugging"] = {
            "score": round(max(0.0, 1.0 - len(hints) / max(len(own_code), 1)), 2),
            "evidence": [f"{len(own_code)} question(s) about their own code, "
                         f"{len(hints)} hint(s) taken"],
            "hits": len(hints),
        }
        factual = len(interactions) - len(own_code)
        scores["independence"] = {
            "score": round(1.0 - _ratio(factual, len(interactions)), 2),
            "evidence": [f"{len(own_code)} of {len(interactions)} questions were about "
                         f"their own program rather than about Python"],
            "hits": factual,
        }

    # Reflection: every difference needs an explanation, so the question is how
    # many they wrote rather than leaving the submission blocked.
    if diffs:
        explained = [d for d in diffs if (d.student_explanation or "").strip()]
        scores["reflection"] = {
            "score": round(_ratio(len(explained), len(diffs)), 2),
            "evidence": [f"explained {len(explained)} of {len(diffs)} difference(s) "
                         f"in their own words"],
            "hits": len(diffs) - len(explained),
        }

    solved = [t for t in timings if t["solved"]]
    if solved:
        scores.setdefault("specification", {"score": 1.0, "evidence": [], "hits": 0})
    return scores


# --------------------------------------------------------------------------
# The plan. One entry per area, with a route through a strength when there is
# one, so the advice is specific to this student rather than generic.
# --------------------------------------------------------------------------
PLANS = {
    "edge_cases": {
        "title": "Decide the boundaries before you write the loop",
        "steps": [
            "Before any code, write one line each for: empty input, zero, and a negative value.",
            "For each one, say what comes back — not what the code happens to do.",
            "After it runs, check the empty case is handled by a line you wrote on purpose.",
        ],
        "via": {
            "specification": "You already write a clear goal and types quickly. Spend the same "
                             "two minutes on the boundary before you start.",
            "debugging": "You debug your own code well. Apply that to the edge case first, "
                         "while it is still cheap to change.",
        },
    },
    "types": {
        "title": "Name the return type before the function exists",
        "steps": [
            "Write the return type in your specification as a concrete Python type.",
            "After your first working version, print `type(result)` and compare it.",
            "If they differ, decide which one is right and change the other.",
        ],
        "via": {
            "examples": "Your examples are concrete. Read the type straight off your own "
                        "example and make the specification say it.",
        },
    },
    "examples": {
        "title": "Make your example prove more than one thing",
        "steps": [
            "Pick an input where a wrong implementation would give a different answer.",
            "For ordering problems, use a non-adjacent duplicate like [3, 1, 3, 2].",
            "Write the exact output, with brackets and types, not a description of it.",
        ],
        "via": {
            "specification": "You state the goal clearly. Turn that sentence into the one "
                             "input that would catch it being wrong.",
        },
    },
    "specification": {
        "title": "Say the whole thing in one sentence first",
        "steps": [
            "Write one sentence: what goes in, what comes back, and what it does.",
            "Name the input exactly as the starter code names it.",
            "Only then answer the gate's questions, one at a time.",
        ],
        "via": {
            "debugging": "You work problems out well once you are coding. Moving ten minutes "
                         "of that thinking earlier is the whole trick.",
        },
    },
    "debugging": {
        "title": "Ask the question the tutor would ask you",
        "steps": [
            "Before asking, write down what you expected that line to produce.",
            "Then write what it actually produced. Print it if you are unsure.",
            "Most of the time the gap between those two sentences is the bug.",
        ],
        "via": {
            "specification": "Your specification is precise. Use it as the expected value: "
                             "it already says what should come back.",
        },
    },
    "independence": {
        "title": "Separate 'what does Python do' from 'what does my code do'",
        "steps": [
            "Ask Python questions freely — those are free and fast to answer.",
            "For your own code, try one print statement before asking.",
            "Keep the hint button for when you are properly stuck, not when you are impatient.",
        ],
        "via": {
            "reflection": "You explain your own work well. That same explaining, done before "
                          "you ask, usually answers the question.",
        },
    },
    "reflection": {
        "title": "Write the explanation while it is still fresh",
        "steps": [
            "For each difference, write one sentence about why the code did what it did.",
            "Say whether your specification or your code was the one you meant.",
            "Do it before you fix it — afterwards you will not remember what you thought.",
        ],
        "via": {
            "debugging": "You already work out what went wrong. Writing that down is one extra "
                         "sentence, and it is the part that transfers.",
        },
    },
}


def _build_plan(weaknesses: list[dict], strengths: list[dict]) -> list[dict]:
    strong = [s["area"] for s in strengths]
    plan = []
    for weak in weaknesses[:3]:
        template = PLANS.get(weak["area"])
        if not template:
            continue
        route = next((template["via"][s] for s in strong if s in template.get("via", {})), None)
        plan.append({
            "area": weak["area"],
            "area_label": AREAS.get(weak["area"], weak["area"]),
            "title": template["title"],
            "because": weak["evidence"][0] if weak["evidence"] else "",
            "leaning_on": route,
            "steps": template["steps"],
        })
    return plan


# --------------------------------------------------------------------------
# Signals behind the tier
# --------------------------------------------------------------------------
def _signals(timings: list[dict], diffs: list[DiffResult],
             interactions: list[Interaction], class_norms: dict) -> list[dict]:
    """Each signal carries its own number, the class comparison and the rows it
    came from, because the tier is only defensible if every point is."""
    out: list[dict] = []

    def add(key, label, severity, points, value, compared, proof):
        out.append({"id": key, "label": label, "severity": severity, "points": points,
                    "value": value, "compared": compared, "proof": proof})

    # 1. unexplained drift
    if diffs:
        drift = [d for d in diffs if d.category == "drift"]
        share = _ratio(len(drift), len(diffs))
        hot = share >= HIGH_DRIFT_SHARE and len(drift) >= 2
        add("drift", "Unnoticed drift from their own specification",
            "concern" if hot else "ok", 2 if hot else 0,
            f"{len(drift)} of {len(diffs)} differences ({share:.0%})",
            f"class average {class_norms.get('drift_share', 0):.0%}",
            [f"{(d.spec_item_key or '')[:70]}" for d in drift[:4]])

    # 2. hints
    hints = [i for i in interactions if i.response_mode == "hint"]
    if interactions:
        hot = len(hints) >= MANY_HINTS
        add("hints", "Direct hints taken", "concern" if hot else "ok",
            2 if hot else 0, f"{len(hints)} hint(s)",
            f"class average {class_norms.get('hints', 0):.1f}",
            [i.student_message[:70] for i in hints[:3]] or ["none taken"])

    # 3. question mix
    if interactions:
        factual = [i for i in interactions if i.classified_type == "factual"]
        share = _ratio(len(factual), len(interactions))
        hot = share >= HIGH_FACTUAL_SHARE and len(interactions) >= 4
        add("question_mix", "Questions about Python rather than their own code",
            "watch" if hot else "ok", 1 if hot else 0,
            f"{len(factual)} of {len(interactions)} ({share:.0%})",
            f"class average {class_norms.get('factual_share', 0):.0%}",
            [i.student_message[:70] for i in factual[:3]] or ["none"])

    # 4. retries
    retries = max((t["submissions"] for t in timings), default=0)
    if retries:
        hot = retries >= MANY_RETRIES
        add("retries", "Submissions before the differences cleared",
            "watch" if hot else "ok", 1 if hot else 0,
            f"{retries} submission(s)",
            f"class average {class_norms.get('submissions', 0):.1f}",
            [f"{t['entry_point'] or 'assignment'}: {t['submissions']} submission(s)"
             for t in timings if t["submissions"]])

    # 5. time against the class
    spans = [t["total_seconds"] for t in timings if t["total_seconds"]]
    norm = class_norms.get("total_seconds")
    if spans and norm:
        worst = max(spans)
        hot = worst >= norm * SLOW_FACTOR
        add("time", "Time spent against the class", "watch" if hot else "ok",
            1 if hot else 0, _human(worst), f"class median {_human(int(norm))}",
            [f"{t['entry_point'] or 'assignment'}: {_human(t['total_seconds'])}"
             for t in timings if t["total_seconds"]])

    # 6. abandoned work
    stalled = [t for t in timings if t["locked"] and not t["submitted"]]
    if stalled:
        add("stalled", "Specifications locked but never submitted", "concern", 2,
            f"{len(stalled)} assignment(s)", "",
            [t["entry_point"] or f"assignment {t['assignment_id']}" for t in stalled])

    return out


def _human(seconds: int | None) -> str:
    if not seconds:
        return "—"
    if seconds < 90:
        return f"{seconds}s"
    minutes = seconds / 60
    if minutes < 90:
        return f"{minutes:.0f} min"
    return f"{minutes / 60:.1f} h"


def tier_for(signals: list[dict]) -> tuple[str, int]:
    score = sum(s["points"] for s in signals)
    if score >= AT_RISK_SCORE:
        return "at_risk", score
    if score > NEEDS_ATTENTION_SCORE:
        return "needs_attention", score
    return "on_track", score


# --------------------------------------------------------------------------
# The public calls
# --------------------------------------------------------------------------
def student_insights(db: Session, student_id: int, *, viewer: str = "student",
                     class_norms: dict | None = None,
                     assignment_ids: set[int] | None = None) -> dict:
    """`assignment_ids` limits everything to those exercises, which is how the
    teacher dashboard shows one subject at a time. None means all of them."""
    student = db.get(Student, student_id)
    if student is None:
        raise LookupError("No such student.")

    spec_query = select(Specification).where(Specification.student_id == student_id)
    if assignment_ids is not None:
        spec_query = spec_query.where(Specification.assignment_id.in_(assignment_ids or {-1}))
    specs = db.scalars(
        spec_query.order_by(Specification.assignment_id, Specification.version)
    ).all()

    by_assignment: dict[int, list[Specification]] = {}
    for spec in specs:
        by_assignment.setdefault(spec.assignment_id, []).append(spec)

    timings = [attempt_timing(db, student_id, aid, rows)
               for aid, rows in sorted(by_assignment.items())]

    spec_ids = [s.id for s in specs]
    interactions = db.scalars(
        select(Interaction).where(Interaction.specification_id.in_(spec_ids))
        .order_by(Interaction.timestamp)
    ).all() if spec_ids else []
    diffs = db.scalars(
        select(DiffResult).where(DiffResult.specification_id.in_(spec_ids))
    ).all() if spec_ids else []

    gap_asks: Counter = Counter()
    for rows in by_assignment.values():
        for key, count in (rows[-1].gap_ask_counts or {}).items():
            gap_asks[key] = max(gap_asks[key], count)

    norms = class_norms if class_norms is not None else class_norms_for(
        db, student.course_id, assignment_ids)
    areas = _area_scores(diffs, interactions, timings, gap_asks)

    ranked = sorted(areas.items(), key=lambda kv: kv[1]["score"], reverse=True)
    strengths = [{"area": a, "label": AREAS.get(a, a), **v} for a, v in ranked if v["score"] >= 0.75][:3]
    weaknesses = [{"area": a, "label": AREAS.get(a, a), **v}
                  for a, v in reversed(ranked) if v["score"] < 0.6][:3]

    payload = {
        "student": {"id": student.id, "name": student.name, "cohort": student.cohort},
        "timings": timings,
        "totals": {
            "assignments_started": len(timings),
            "assignments_submitted": sum(1 for t in timings if t["submitted"]),
            "assignments_solved": sum(1 for t in timings if t["solved"]),
            "specification_versions": len(specs),
            "questions": len(interactions),
            "questions_own_code": sum(1 for i in interactions
                                      if i.classified_type == "problem_solving"),
            "hints": sum(1 for i in interactions if i.response_mode == "hint"),
            "differences": len(diffs),
            "differences_explained": sum(1 for d in diffs
                                         if (d.student_explanation or "").strip()),
            "mean_specification_seconds": _mean_of(timings, "specification_seconds"),
            "mean_coding_seconds": _mean_of(timings, "coding_seconds"),
            "mean_total_seconds": _mean_of(timings, "total_seconds"),
            "mean_retry_gap_seconds": _mean_of(timings, "mean_retry_gap_seconds"),
        },
        "class_norms": norms,
        "areas": {a: v for a, v in areas.items()},
        "strengths": strengths,
        "weaknesses": weaknesses,
        "plan": _build_plan(weaknesses, strengths),
        "difference_mix": dict(Counter(d.category for d in diffs)),
    }

    signals = _signals(timings, diffs, interactions, norms)
    if viewer == "instructor":
        if not timings:
            tier, score, signals = "not_started", 0, []
        else:
            tier, score = tier_for(signals)
        payload["status"] = {
            "tier": tier, "label": TIER_LABELS[tier], "score": score,
            "signals": signals,
            "thresholds": {"needs_attention": NEEDS_ATTENTION_SCORE, "at_risk": AT_RISK_SCORE},
        }
    return payload


def _mean_of(rows: list[dict], key: str) -> int | None:
    values = [r[key] for r in rows if r.get(key)]
    return int(mean(values)) if values else None


def class_norms_for(db: Session, course_id: str,
                    assignment_ids: set[int] | None = None) -> dict:
    """Class-level averages, used both as chart baselines and as the comparison
    every signal is measured against."""
    students = db.scalars(select(Student).where(Student.course_id == course_id)).all()
    totals, drift_shares, factual_shares, hints, submissions = [], [], [], [], []
    difference_counts = []

    for student in students:
        spec_query = select(Specification).where(Specification.student_id == student.id)
        if assignment_ids is not None:
            spec_query = spec_query.where(Specification.assignment_id.in_(assignment_ids or {-1}))
        spec_ids = [s.id for s in db.scalars(spec_query).all()]
        if not spec_ids:
            continue
        diffs = db.scalars(select(DiffResult)
                           .where(DiffResult.specification_id.in_(spec_ids))).all()
        rows = db.scalars(select(Interaction)
                          .where(Interaction.specification_id.in_(spec_ids))).all()
        attempts = db.scalars(select(SubmissionAttempt)
                              .where(SubmissionAttempt.specification_id.in_(spec_ids))).all()
        # Per ASSIGNMENT, not per student: the signal this norm is compared
        # against is "submissions on one assignment", and comparing that to a
        # total across three assignments makes every student look diligent.
        difference_counts.append(len(diffs))
        if diffs:
            drift_shares.append(_ratio(sum(1 for d in diffs if d.category == "drift"), len(diffs)))
        if rows:
            factual_shares.append(
                _ratio(sum(1 for r in rows if r.classified_type == "factual"), len(rows)))
            hints.append(sum(1 for r in rows if r.response_mode == "hint"))
        if attempts:
            per_spec: Counter = Counter(a.specification_id for a in attempts)
            submissions.extend(per_spec.values())

        specs = db.scalars(select(Specification)
                           .where(Specification.student_id == student.id)
                           .order_by(Specification.version)).all()
        per_assignment: dict[int, list[Specification]] = {}
        for spec in specs:
            per_assignment.setdefault(spec.assignment_id, []).append(spec)
        for aid, group in per_assignment.items():
            span = _span(group[0].created_at, group[-1].submitted_at)
            if span:
                totals.append(span)

    return {
        "students": len(students),
        "total_seconds": int(median(totals)) if totals else None,
        "total_seconds_mean": int(mean(totals)) if totals else None,
        "drift_share": round(mean(drift_shares), 3) if drift_shares else 0.0,
        "factual_share": round(mean(factual_shares), 3) if factual_shares else 0.0,
        "hints": round(mean(hints), 2) if hints else 0.0,
        "submissions": round(mean(submissions), 2) if submissions else 0.0,
        "differences": round(mean(difference_counts), 1) if difference_counts else 0.0,
    }


def class_insights(db: Session, course_id: str,
                   assignment_ids: set[int] | None = None) -> dict:
    """The roster with a tier beside every name, plus the class distribution."""
    norms = class_norms_for(db, course_id, assignment_ids)
    students = db.scalars(
        select(Student).where(Student.course_id == course_id).order_by(Student.id)
    ).all()

    roster = []
    for student in students:
        insight = student_insights(db, student.id, viewer="instructor", class_norms=norms,
                                   assignment_ids=assignment_ids)
        status = insight["status"]
        roster.append({
            "student_id": student.id,
            "name": student.name,
            "cohort": student.cohort,
            "tier": status["tier"],
            "label": status["label"],
            "score": status["score"],
            "headline": _headline(status["signals"]),
            "questions": insight["totals"]["questions"],
            "hints": insight["totals"]["hints"],
            "differences": insight["totals"]["differences"],
            "mean_total_seconds": insight["totals"]["mean_total_seconds"],
            "assignments_solved": insight["totals"]["assignments_solved"],
            "assignments_started": insight["totals"]["assignments_started"],
        })

    distribution = Counter(r["tier"] for r in roster)
    return {
        "course_id": course_id,
        "class_norms": norms,
        "distribution": {tier: distribution.get(tier, 0) for tier in TIERS},
        "roster": roster,
    }


def _headline(signals: list[dict]) -> str:
    """The single worst signal, phrased for a table cell."""
    flagged = [s for s in signals if s["points"] > 0]
    if not flagged:
        return "No flags"
    worst = max(flagged, key=lambda s: s["points"])
    return f"{worst['label']} — {worst['value']}"
