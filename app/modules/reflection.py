"""Step 3 — Reflection Engine (Section 3.3).

Runs once, at submission. It compares the locked Specification against the final
CodeSnapshot and produces one DiffResult per discrepancy. It does not fix
anything: it surfaces the discrepancy and requires the student to explain it in
their own words before the submission is accepted. The friction is the feature.

Three steps:
  1. Behavioral diff — plain subprocess execution of the declared examples and
     edge cases. No AI call.
  2. One DiffResult per failing or unaddressed case, plus structural mismatches
     between declared output types and what the code actually returns.
  3. Classify each DiffResult as drift / revision / bug, using the interaction
     log as evidence. One `generate_structured()` call per DiffResult — low
     volume, comfortably inside the free tier.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm import CLASSIFICATION, DIALOGUE, generate_structured
from app.llm import prompts
from app.models import (
    CodeSnapshot,
    DiffResult,
    Interaction,
    Specification,
    SubmissionAttempt,
)
from app.modules import sandbox

CATEGORIES = ("drift", "revision", "bug")

_TYPE_ALIASES = {
    "int": {"int"}, "integer": {"int"}, "float": {"float", "int"}, "number": {"int", "float"},
    "str": {"str"}, "string": {"str"}, "bool": {"bool"}, "boolean": {"bool"},
    "list": {"list"}, "array": {"list"}, "tuple": {"tuple"}, "dict": {"dict"},
    "set": {"set"}, "none": {"NoneType"}, "nonetype": {"NoneType"},
}


# --------------------------------------------------------------------------
# Step 1 — behavioral diff
# --------------------------------------------------------------------------
def materialize_edge_cases(content: dict) -> list[dict]:
    """Turn declared edge cases into runnable inputs. One classification-tier
    call per submission — this is what lets the engine detect the classic
    'never handled empty input' drift instead of only checking happy paths."""
    edge_cases = content.get("edge_cases") or []
    if not edge_cases:
        return []
    system, schema = prompts.MATERIALIZE_EDGE_CASES
    payload = json.dumps(
        {
            "edge_cases": edge_cases,
            "inputs": content.get("inputs") or [],
            "outputs": content.get("outputs") or [],
            "examples": content.get("examples") or [],
        },
        ensure_ascii=False,
    )
    raw = generate_structured(system, payload, schema,
                              task="materialize_edge_cases", tier=CLASSIFICATION)
    cases = raw.get("cases")
    return cases if isinstance(cases, list) else []


def behavioral_diff(content: dict, code: str, entry_point: str) -> list[dict]:
    """Run every declared case. Returns one row per case with pass/fail plus
    enough detail for the classifier and for the student to see what happened."""
    rows: list[dict] = []
    declared_types = _declared_output_types(content)

    for index, example in enumerate(content.get("examples") or []):
        if not isinstance(example, dict):
            continue
        result = sandbox.run_case(code, entry_point, str(example.get("input", "")))
        expected = example.get("output")
        rows.append(_row(
            kind="example", key=f"examples[{index}]",
            source=f"example {example.get('input')} -> {expected}",
            input_spec=str(example.get("input", "")), expected=expected,
            result=result, declared_types=declared_types, expectation_declared=expected is not None,
        ))

    for case in materialize_edge_cases(content):
        if not isinstance(case, dict):
            continue
        result = sandbox.run_case(code, entry_point, str(case.get("input", "")))
        rows.append(_row(
            kind="edge_case", key=f"edge_case:{case.get('edge_case')}",
            source=f"edge case {case.get('edge_case')!r} (as {case.get('input')})",
            input_spec=str(case.get("input", "")), expected=case.get("expected"),
            result=result, declared_types=declared_types,
            expectation_declared=bool(case.get("declared")) and case.get("expected") is not None,
        ))

    return rows


def _declared_output_types(content: dict) -> set[str]:
    names: set[str] = set()
    for output in content.get("outputs") or []:
        raw = (output.get("type") if isinstance(output, dict) else str(output)) or ""
        base = str(raw).split("[")[0].strip().lower()
        names |= _TYPE_ALIASES.get(base, set())
    return names


def _mismatch(expected, result) -> str:
    """"expected '1,2', got '1,2'" is a real message this produced, and there is
    nothing a student can do with it. When the two read alike the difference is
    the type, so say the type."""
    expected_value, parsed = sandbox.parse_expected(expected)
    want = repr(expected_value) if parsed else str(expected)
    got = result.value_repr
    if _looks_alike(want, got):
        want_type = type(expected_value).__name__ if parsed else "str"
        return (f"expected {want} ({want_type}), got {got} "
                f"({result.value_type or 'unknown'}) — same values, different type")
    return f"expected {want}, got {got}"


def _looks_alike(want: str | None, got: str | None) -> bool:
    strip = lambda s: "".join(str(s or "").split()).strip("'\"[]()")  # noqa: E731
    return strip(want) == strip(got)


def _row(*, kind, key, source, input_spec, expected, result, declared_types,
         expectation_declared) -> dict:
    failure: str | None = None

    if result.error:
        failure = f"raised {result.error}"
    elif expectation_declared and not sandbox.matches(result.value_repr, expected):
        failure = _mismatch(expected, result)
    elif declared_types and result.value_type and result.value_type not in declared_types:
        failure = (
            f"declared output type {'/'.join(sorted(declared_types))} but returned "
            f"{result.value_type} ({result.value_repr})"
        )
    elif not expectation_declared and kind == "edge_case":
        # Ran clean and the type fits, but the specification never says what
        # should happen here. Not a failure — recorded so the dashboard can
        # show coverage honestly.
        failure = None

    return {
        "kind": kind,
        "key": key,
        "source": source,
        "input": input_spec,
        "expected": expected,
        "actual": result.value_repr,
        "actual_type": result.value_type,
        "error": result.error,
        "timed_out": result.timed_out,
        "stdout": result.stdout,
        "expectation_declared": expectation_declared,
        "passed": failure is None,
        "failure": failure,
    }


# --------------------------------------------------------------------------
# Step 3 — classify
# --------------------------------------------------------------------------
def classify_discrepancy(spec_item: str, test_result: dict, interaction_log: list[dict]) -> dict:
    system, schema = prompts.CLASSIFY_DIFF
    payload = json.dumps(
        {
            "specification_item": spec_item,
            "test_result": test_result,
            "interaction_log": interaction_log,
        },
        ensure_ascii=False,
    )
    raw = generate_structured(system, payload, schema, task="classify_diff", tier=CLASSIFICATION)
    category = raw.get("category")
    if category not in CATEGORIES:
        category = "bug" if test_result.get("error") else "drift"
    evidence = raw.get("evidence")
    return {
        "category": category,
        "evidence": str(evidence)[:1000] if evidence else None,
        "description": str(raw.get("description") or "").strip()
        or f"{spec_item}: {test_result.get('failure')}",
    }


def diagnostic_question(spec_item: str, test_result: dict, code: str,
                        category: str, prior: list[str]) -> dict:
    """One Socratic question per discrepancy, to help the student diagnose it.

    Dialogue tier. This is the call where quality matters most in reflection: a
    question that names the cause hands them the answer and the explanation they
    write afterwards is worthless.
    """
    system, schema = prompts.DIAGNOSTIC_QUESTION
    payload = json.dumps(
        {
            "specification_item": spec_item,
            "category": category,
            "expected": test_result.get("expected"),
            "actual": test_result.get("actual"),
            "error": test_result.get("error"),
            "input": test_result.get("input"),
            "student_code": (code or "")[:6000],
            "prior_questions": prior,
        },
        ensure_ascii=False,
    )
    raw = generate_structured(system, payload, schema,
                              task="diagnostic_question", tier=DIALOGUE)
    question = (raw.get("question") or "").strip()
    return {
        "question": question or "What did you expect that value to be for this input?",
        "targets": (raw.get("targets") or "").strip(),
    }


def discuss(db: Session, diff: DiffResult, message: str) -> dict:
    """A reply in the diagnosis thread for one discrepancy.

    Always a counter-question — reflection surfaces and probes, it never fixes.
    Logged as an Interaction so the transcript and the dependency profile stay
    one record rather than two.
    """
    text = (message or "").strip()
    if not text:
        raise ValueError("Say what you found — even 'I don't see it' is a starting point.")

    prior = db.scalars(
        select(Interaction)
        .where(Interaction.diff_result_id == diff.id)
        .order_by(Interaction.timestamp, Interaction.id)
    ).all()

    snapshot = db.get(CodeSnapshot, diff.code_snapshot_id) if diff.code_snapshot_id else None
    # The opening question lives on the DiffResult, not in the interaction log.
    # Leave it out of `prior` and the first reply gets the same question back.
    already_asked = ([diff.diagnostic_question] if diff.diagnostic_question else []) \
        + [row.ai_response for row in prior]
    generated = diagnostic_question(
        diff.description, diff.test_detail or {}, snapshot.content if snapshot else "",
        diff.category, already_asked,
    )

    interaction = Interaction(
        specification_id=diff.specification_id,
        student_id=diff.specification.student_id,
        student_message=text,
        classified_type="problem_solving",
        ai_response=generated["question"],
        response_mode="diagnostic",
        thread_round=len(prior) + 1,
        diff_result_id=diff.id,
        reasoning="reflection diagnosis",
    )
    db.add(interaction)
    db.commit()
    db.refresh(interaction)

    return {
        "diff_result_id": diff.id,
        "question": generated["question"],
        "targets": generated["targets"],
        "round": len(prior) + 1,
        "thread": diagnosis_thread(db, diff.id),
    }


def diagnosis_thread(db: Session, diff_result_id: int) -> list[dict]:
    rows = db.scalars(
        select(Interaction)
        .where(Interaction.diff_result_id == diff_result_id)
        .order_by(Interaction.timestamp, Interaction.id)
    ).all()
    return [{"student_message": r.student_message, "ai_response": r.ai_response,
             "round": r.thread_round} for r in rows]


def _interaction_log(db: Session, specification_id: int) -> list[dict]:
    rows = db.scalars(
        select(Interaction)
        .where(Interaction.specification_id == specification_id)
        .order_by(Interaction.timestamp, Interaction.id)
    ).all()
    return [
        {
            "timestamp": row.timestamp.isoformat() if row.timestamp else None,
            "student_message": row.student_message,
            "classified_type": row.classified_type,
            "response_mode": row.response_mode,
            "ai_response": row.ai_response,
        }
        for row in rows
    ]


# --------------------------------------------------------------------------
# Submission
# --------------------------------------------------------------------------
def submit(db: Session, spec: Specification, code: str | None = None) -> dict:
    """Run the full pipeline and return the DiffResults awaiting explanation."""
    if not spec.is_locked:
        raise ValueError("Specification must be locked before submission.")

    checker = spec.assignment.checker
    entry_point = spec.assignment.entry_point
    if checker == "python" and not entry_point:
        raise ValueError(
            "This assignment has no rubric.entry_point, so the behavioral diff "
            "has no function to call."
        )

    content = spec.content or {}
    # Only the Python checker needs declared cases: there is nothing to run
    # without them. A maths answer is checked against the instructor's expected
    # answer, which exists whether or not the student wrote an example.
    if checker == "python" and not content.get("examples") and not content.get("edge_cases"):
        # Without a declared case there is nothing to run, and returning a clean
        # bill of health would be a lie: the code was never checked against
        # anything. Say so instead of silently accepting the submission.
        raise ValueError(
            "Your locked specification declares no examples and no edge cases, so "
            "there is nothing to run your code against. Add at least one concrete "
            "input/output example to the specification before submitting."
        )

    snapshot = _final_snapshot(db, spec, code)

    # Re-running submission replaces the previous verdict rather than stacking.
    for existing in list(spec.diff_results):
        db.delete(existing)
    db.flush()

    # Phase 3 is the one place the subject matters. Everything downstream — the
    # classifier, the dashboard, the insights engine, the student's own page —
    # works on the rows these produce without knowing which ran.
    if checker == "math":
        from app.modules import math_check
        rows = math_check.check(spec.content or {}, snapshot.content,
                                spec.assignment.rubric or {})
    else:
        rows = behavioral_diff(spec.content or {}, snapshot.content, entry_point)
    failures = [row for row in rows if not row["passed"]]

    log = _interaction_log(db, spec.id) if failures else []
    created: list[DiffResult] = []
    for row in failures:
        verdict = classify_discrepancy(row["source"], row, log)
        diff = DiffResult(
            specification_id=spec.id,
            code_snapshot_id=snapshot.id,
            category=verdict["category"],
            description=verdict["description"],
            evidence=verdict["evidence"],
            spec_item_key=row["key"],
            test_detail=row,
            # The opening question, ready before the student even reads the card.
            diagnostic_question=diagnostic_question(
                verdict["description"], row, snapshot.content,
                verdict["category"], [])["question"],
        )
        db.add(diff)
        created.append(diff)

    # Retry history. The DiffResult rows above are the CURRENT verdict and get
    # replaced on every submission; this row is kept, so "they got there on the
    # fourth try, forty minutes apart" stays answerable.
    previous = db.scalars(
        select(SubmissionAttempt)
        .where(SubmissionAttempt.specification_id == spec.id)
        .order_by(SubmissionAttempt.attempt_number.desc())
        .limit(1)
    ).first()
    now = datetime.now(timezone.utc)
    gap = None
    if previous is not None and previous.timestamp is not None:
        before = previous.timestamp
        if before.tzinfo is None:  # SQLite hands back naive datetimes
            before = before.replace(tzinfo=timezone.utc)
        gap = max(0, int((now - before).total_seconds()))
    db.add(SubmissionAttempt(
        specification_id=spec.id,
        student_id=spec.student_id,
        assignment_id=spec.assignment_id,
        code_snapshot_id=snapshot.id,
        attempt_number=(previous.attempt_number + 1) if previous else 1,
        timestamp=now,
        cases_run=len(rows),
        cases_passed=len(rows) - len(failures),
        differences=len(created),
        seconds_since_previous=gap,
    ))

    db.commit()
    for diff in created:
        db.refresh(diff)
    db.refresh(spec)

    return {
        "specification_id": spec.id,
        "code_snapshot_id": snapshot.id,
        "cases_run": len(rows),
        "cases_passed": len(rows) - len(failures),
        "results": [_case_public(row) for row in rows],
        "diff_results": [diff_public(d) for d in created],
        "explanations_required": len(created),
        "can_finalize": not created,
    }


def _final_snapshot(db: Session, spec: Specification, code: str | None) -> CodeSnapshot:
    if code is not None:
        snapshot = CodeSnapshot(specification_id=spec.id, content=code, is_final=True)
        db.add(snapshot)
        db.flush()
        return snapshot
    latest = db.scalars(
        select(CodeSnapshot)
        .where(CodeSnapshot.specification_id == spec.id)
        .order_by(CodeSnapshot.timestamp.desc(), CodeSnapshot.id.desc())
        .limit(1)
    ).first()
    if latest is None:
        raise ValueError("No code has been saved for this specification yet.")
    latest.is_final = True
    db.flush()
    return latest


def record_explanation(db: Session, diff: DiffResult, explanation: str) -> dict:
    text = (explanation or "").strip()
    if len(text) < 15:
        raise ValueError("Write a sentence or two in your own words — that is the point of this step.")
    diff.student_explanation = text
    db.commit()
    db.refresh(diff)
    return diff_public(diff)


def submission_status(db: Session, spec: Specification) -> dict:
    diffs = list(spec.diff_results)
    outstanding = [d for d in diffs if not (d.student_explanation or "").strip()]
    ready = bool(diffs) or bool(spec.snapshots)
    return {
        "specification_id": spec.id,
        "diff_results_total": len(diffs),
        "explanations_outstanding": len(outstanding),
        "outstanding_ids": [d.id for d in outstanding],
        "can_finalize": ready and not outstanding,
        "finalized_at": spec.submitted_at.isoformat() if spec.submitted_at else None,
        # The discrepancies themselves, so a student returning to the reflection
        # phase after a reload sees their results instead of an empty panel.
        "diff_results": [diff_public(d) for d in diffs],
        "submitted": bool(spec.snapshots and any(s.is_final for s in spec.snapshots)),
    }


def finalize(db: Session, spec: Specification) -> dict:
    from app.models import utcnow

    status = submission_status(db, spec)
    if not status["can_finalize"]:
        raise ValueError(
            f"{status['explanations_outstanding']} discrepancy explanation(s) still missing."
        )
    spec.submitted_at = utcnow()
    db.commit()
    db.refresh(spec)
    return submission_status(db, spec)


# --------------------------------------------------------------------------
def diff_public(diff: DiffResult) -> dict:
    return {
        "id": diff.id,
        "category": diff.category,
        "description": diff.description,
        "evidence": diff.evidence,
        "spec_item_key": diff.spec_item_key,
        "student_explanation": diff.student_explanation,
        "instructor_flag": diff.instructor_flag,
        "diagnostic_question": diff.diagnostic_question,
        "test_detail": _case_public(diff.test_detail or {}),
    }


def _case_public(row: dict) -> dict:
    keys = ("kind", "source", "input", "expected", "actual", "actual_type",
            "error", "timed_out", "passed", "failure", "expectation_declared")
    return {k: row.get(k) for k in keys if k in row}
