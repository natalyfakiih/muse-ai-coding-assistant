"""Step 2 — Tutor Layer (Section 3.2).

Active while the student writes code in their own editor. There is no
autocomplete and no inline generation here — the only AI surface is the chat
box, and every message through it is classified before anything is generated.

This is the highest-volume caller of `generate_structured()`: one classification
call per student message, plus one generation call. It is also where Mistral's
free-tier RPM cap will bite first during a live class, which is exactly what the
Groq fallback is for.
"""
from __future__ import annotations

import difflib
import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.llm import CLASSIFICATION, DIALOGUE, generate_structured
from app.llm import prompts
from app.models import CodeSnapshot, Interaction, Specification

FACTUAL = "factual"
PROBLEM_SOLVING = "problem_solving"

MODE_ANSWER = "answer"
MODE_COUNTER_QUESTION = "counter_question"
MODE_HINT = "hint"

_TRACEBACK = re.compile(r"traceback|most recent call last|^\s*File \".*\", line \d+", re.I | re.M)
_PY_KEYWORDS = frozenset("""
and as assert async await break class continue def del elif else except False finally for from
global if import in is lambda None nonlocal not or pass raise return True try while with yield
int str list dict set tuple float bool len range print append return self None true false
""".split())


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------
def classify(student_message: str, current_code_diff: str) -> dict:
    system, schema = prompts.CLASSIFY_QUESTION
    payload = json.dumps(
        {"student_message": student_message, "current_code_diff": current_code_diff or ""},
        ensure_ascii=False,
    )
    raw = generate_structured(system, payload, schema, task="classify_question", tier=CLASSIFICATION)
    kind = raw.get("type")
    if kind not in (FACTUAL, PROBLEM_SOLVING):
        kind = PROBLEM_SOLVING  # unreadable answer defaults to the safe side
    confidence = raw.get("confidence")
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = None
    return {"type": kind, "confidence": confidence,
            "reasoning": str(raw.get("reasoning") or "")[:300]}


def student_identifiers(code: str) -> set[str]:
    """Names the student defined in their own file."""
    names: set[str] = set()
    if not code:
        return names
    for pattern in (r"\bdef\s+(\w+)", r"\bclass\s+(\w+)", r"^\s*(\w+)\s*=", r"\bfor\s+(\w+)\s+in\b"):
        names.update(re.findall(pattern, code, re.M))
    return {n for n in names if len(n) > 1 and n not in _PY_KEYWORDS}


def deterministic_override(message: str, code: str) -> str | None:
    """The Section 3.2 anti-gaming rule, enforced in code rather than left to
    the prompt: a message that names the student's own identifiers or pastes a
    traceback is problem_solving even when phrased as a definition question.
    """
    if _TRACEBACK.search(message or ""):
        return "traceback in message"
    mentioned = student_identifiers(code) & set(re.findall(r"\b\w+\b", message or ""))
    if mentioned:
        return f"references own identifier(s): {', '.join(sorted(mentioned)[:3])}"
    return None


# --------------------------------------------------------------------------
# Response generation
# --------------------------------------------------------------------------
def answer_factual(student_message: str, code: str) -> dict:
    system, schema = prompts.FACTUAL_ANSWER
    payload = json.dumps(
        {"student_message": student_message, "student_code_context": (code or "")[:4000]},
        ensure_ascii=False,
    )
    raw = generate_structured(system, payload, schema, task="factual_answer", tier=CLASSIFICATION)
    return {"answer": (raw.get("answer") or "").strip(), "deflect": bool(raw.get("deflect"))}


def socratic_question(student_message: str, code_snippet: str, prior: list[str]) -> dict:
    """The quality-sensitive call path. Upgrade this tier first when there is
    budget — a mediocre Socratic question actively damages the pedagogy."""
    system, schema = prompts.SOCRATIC_QUESTION
    payload = json.dumps(
        {
            "student_message": student_message,
            "relevant_code_snippet": (code_snippet or "")[:4000],
            "prior_counter_questions_this_thread": prior,
        },
        ensure_ascii=False,
    )
    raw = generate_structured(system, payload, schema, task="socratic_question", tier=DIALOGUE)
    question = (raw.get("question") or "").strip()
    return {
        "question": question or "What do you expect that value to be right before that line runs?",
        "targets": (raw.get("targets") or "").strip(),
    }


def direct_hint(student_message: str, code: str, prior: list[str]) -> str:
    system, schema = prompts.DIRECT_HINT
    payload = json.dumps(
        {"student_message": student_message, "student_code": (code or "")[:4000],
         "prior_counter_questions_this_thread": prior},
        ensure_ascii=False,
    )
    raw = generate_structured(system, payload, schema, task="direct_hint", tier=DIALOGUE)
    return (raw.get("hint") or "").strip() or "Re-check the boundary condition in the loop."


# --------------------------------------------------------------------------
# Thread bookkeeping — pure app logic over the interactions table, no AI call.
# --------------------------------------------------------------------------
def open_thread(db: Session, specification_id: int) -> list[Interaction]:
    """Trailing run of counter-questions = the current unresolved issue thread.
    A factual answer or a delivered hint closes the thread."""
    rows = db.scalars(
        select(Interaction)
        .where(Interaction.specification_id == specification_id)
        .order_by(Interaction.timestamp.desc(), Interaction.id.desc())
        .limit(20)
    ).all()
    thread: list[Interaction] = []
    for row in rows:
        if row.response_mode != MODE_COUNTER_QUESTION:
            break
        thread.append(row)
    return list(reversed(thread))


def escape_hatch_available(db: Session, specification_id: int) -> bool:
    return len(open_thread(db, specification_id)) >= settings.TUTOR_ESCAPE_HATCH_ROUNDS


# --------------------------------------------------------------------------
# Code snapshots / diff context
# --------------------------------------------------------------------------
def latest_snapshot(db: Session, specification_id: int) -> CodeSnapshot | None:
    return db.scalars(
        select(CodeSnapshot)
        .where(CodeSnapshot.specification_id == specification_id)
        .order_by(CodeSnapshot.timestamp.desc(), CodeSnapshot.id.desc())
        .limit(1)
    ).first()


def save_snapshot(db: Session, specification_id: int, code: str, is_final: bool = False) -> CodeSnapshot:
    snapshot = CodeSnapshot(specification_id=specification_id, content=code or "", is_final=is_final)
    db.add(snapshot)
    db.flush()
    return snapshot


def unified_diff(previous: str, current: str) -> str:
    if previous == current:
        return ""
    return "".join(
        difflib.unified_diff(
            (previous or "").splitlines(keepends=True),
            (current or "").splitlines(keepends=True),
            fromfile="previous", tofile="current", n=3,
        )
    )[:6000]


# --------------------------------------------------------------------------
# The turn
# --------------------------------------------------------------------------
def handle_message(
    db: Session,
    spec: Specification,
    student_message: str,
    code: str | None = None,
    code_diff: str | None = None,
) -> dict:
    if not spec.is_locked:
        raise ValueError("The tutor is only available once the specification is locked.")

    previous = latest_snapshot(db, spec.id)
    current_code = code if code is not None else (previous.content if previous else "")
    if code is not None and (previous is None or previous.content != code):
        save_snapshot(db, spec.id, code)
    if code_diff is None:
        code_diff = unified_diff(previous.content if previous else "", current_code)

    classification = classify(student_message, code_diff or current_code)
    override_reason = deterministic_override(student_message, current_code)
    overridden = False
    if override_reason and classification["type"] == FACTUAL:
        classification["type"] = PROBLEM_SOLVING
        classification["reasoning"] = f"override: {override_reason}"
        overridden = True

    thread = open_thread(db, spec.id)
    prior_questions = [row.ai_response for row in thread]
    response, mode, targets = "", MODE_ANSWER, None

    if classification["type"] == FACTUAL:
        result = answer_factual(student_message, current_code)
        if result["deflect"]:
            classification["type"] = PROBLEM_SOLVING
            classification["reasoning"] = "deflected: only answerable from own code"
            overridden = True
        else:
            response, mode, targets = result["answer"], MODE_ANSWER, None

    if classification["type"] == PROBLEM_SOLVING:
        generated = socratic_question(student_message, current_code, prior_questions)
        response, mode, targets = generated["question"], MODE_COUNTER_QUESTION, generated["targets"]

    round_number = len(thread) + 1 if mode == MODE_COUNTER_QUESTION else 0

    interaction = Interaction(
        specification_id=spec.id,
        student_id=spec.student_id,
        student_message=student_message,
        classified_type=classification["type"],
        ai_response=response,
        response_mode=mode,
        confidence=classification["confidence"],
        reasoning=classification["reasoning"],
        thread_round=round_number,
    )
    db.add(interaction)
    db.commit()
    db.refresh(interaction)

    return {
        "interaction_id": interaction.id,
        "classified_type": classification["type"],
        "confidence": classification["confidence"],
        "reasoning": classification["reasoning"],
        "classifier_overridden": overridden,
        "response_mode": mode,
        "response": response,
        "targets": targets,
        "thread_round": round_number,
        "escape_hatch_available": round_number >= settings.TUTOR_ESCAPE_HATCH_ROUNDS,
    }


def use_escape_hatch(db: Session, spec: Specification, student_message: str | None = None) -> dict:
    """Direct hint at a logged assist cost, offered after N unresolved rounds.
    Visible later in the Dependency Profile; never graded."""
    thread = open_thread(db, spec.id)
    if len(thread) < settings.TUTOR_ESCAPE_HATCH_ROUNDS:
        raise ValueError(
            f"Escape hatch unlocks after {settings.TUTOR_ESCAPE_HATCH_ROUNDS} "
            f"counter-question rounds on the same issue (currently {len(thread)})."
        )

    snapshot = latest_snapshot(db, spec.id)
    code = snapshot.content if snapshot else ""
    message = student_message or (thread[-1].student_message if thread else "I'm stuck.")
    hint = direct_hint(message, code, [row.ai_response for row in thread])

    interaction = Interaction(
        specification_id=spec.id,
        student_id=spec.student_id,
        student_message=message,
        classified_type=PROBLEM_SOLVING,
        ai_response=hint,
        response_mode=MODE_HINT,
        assist_cost=1,
        thread_round=len(thread) + 1,
        reasoning="escape hatch",
    )
    db.add(interaction)
    db.commit()
    db.refresh(interaction)

    return {
        "interaction_id": interaction.id,
        "response_mode": MODE_HINT,
        "response": hint,
        "assist_cost": 1,
        "rounds_before_hint": len(thread),
    }
