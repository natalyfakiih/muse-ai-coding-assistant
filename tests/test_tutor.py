"""Tutor Layer — routing, the anti-gaming override, and the escape hatch."""
from __future__ import annotations

from app.modules import tutor
from tests.conftest import BUGGY_FIB, lock_specification


def ask(client, spec_id, message, code=BUGGY_FIB):
    return client.post(f"/api/specifications/{spec_id}/interactions",
                       json={"message": message, "code": code}).json()


def test_tutor_is_unavailable_until_the_specification_is_locked(seeded):
    client, student_id, assignment_id = seeded
    client.post(f"/api/assignments/{assignment_id}/specification",
                json={"student_id": student_id, "message": "fibonacci"})
    spec = client.get(f"/api/assignments/{assignment_id}/specification",
                      params={"student_id": student_id}).json()

    response = client.post(f"/api/specifications/{spec['specification_id']}/interactions",
                           json={"message": "how do I start?"})
    assert response.status_code == 409


def test_factual_question_gets_a_direct_answer(seeded):
    client, student_id, assignment_id = seeded
    spec_id = lock_specification(client, student_id, assignment_id)

    result = ask(client, spec_id, "what does .append() return?", code="")
    assert result["classified_type"] == "factual"
    assert result["response_mode"] == "answer"


def test_problem_solving_question_gets_a_counter_question(seeded):
    client, student_id, assignment_id = seeded
    spec_id = lock_specification(client, student_id, assignment_id)

    result = ask(client, spec_id, "why is my loop returning one too many numbers?")
    assert result["classified_type"] == "problem_solving"
    assert result["response_mode"] == "counter_question"
    assert result["response"].strip().endswith("?")


def test_a_question_naming_the_students_own_identifier_is_reclassified():
    """Section 3.2's mitigation, enforced in code: students learn to rephrase
    problem-solving questions as pseudo-factual ones."""
    reason = tutor.deterministic_override("what does fib return for 3?", BUGGY_FIB)
    assert reason and "fib" in reason


def test_a_pasted_traceback_is_reclassified():
    reason = tutor.deterministic_override(
        'Traceback (most recent call last):\n  File "x.py", line 4\nTypeError', "")
    assert reason == "traceback in message"


def test_a_generic_language_question_is_not_reclassified():
    assert tutor.deterministic_override("what is a dict comprehension?", BUGGY_FIB) is None


def test_override_is_applied_through_the_api(seeded):
    client, student_id, assignment_id = seeded
    spec_id = lock_specification(client, student_id, assignment_id)

    result = ask(client, spec_id, "just definitionally, what does out hold here?")
    assert result["classified_type"] == "problem_solving"


def test_escape_hatch_is_locked_until_three_unresolved_rounds(seeded):
    client, student_id, assignment_id = seeded
    spec_id = lock_specification(client, student_id, assignment_id)

    ask(client, spec_id, "why is my loop wrong?")
    early = client.post(f"/api/specifications/{spec_id}/interactions/escape-hatch", json={})
    assert early.status_code == 400

    ask(client, spec_id, "I tried that, still wrong")
    third = ask(client, spec_id, "still stuck on my loop")
    assert third["thread_round"] == 3
    assert third["escape_hatch_available"] is True

    hint = client.post(f"/api/specifications/{spec_id}/interactions/escape-hatch",
                       json={}).json()
    assert hint["response_mode"] == "hint"
    assert hint["assist_cost"] == 1
    assert hint["rounds_before_hint"] == 3


def test_a_factual_answer_closes_the_open_thread(seeded):
    """The counter (thread_round) is per issue, not per session."""
    client, student_id, assignment_id = seeded
    spec_id = lock_specification(client, student_id, assignment_id)

    ask(client, spec_id, "why is my loop wrong?")
    ask(client, spec_id, "I still don't see it in my loop")
    ask(client, spec_id, "what is a list comprehension?", code="")
    after = ask(client, spec_id, "ok but my loop is still wrong")
    assert after["thread_round"] == 1


def test_interactions_are_logged_for_reflection_and_profiling(seeded):
    client, student_id, assignment_id = seeded
    spec_id = lock_specification(client, student_id, assignment_id)
    ask(client, spec_id, "why is my fib loop off?")

    history = client.get(f"/api/specifications/{spec_id}/interactions").json()
    row = history["interactions"][0]
    assert row["student_message"] and row["ai_response"]
    assert row["classified_type"] in ("factual", "problem_solving")
    assert row["response_mode"] in ("answer", "counter_question", "hint")


def test_code_edits_are_snapshotted(seeded):
    client, student_id, assignment_id = seeded
    spec_id = lock_specification(client, student_id, assignment_id)

    client.post(f"/api/specifications/{spec_id}/snapshots", json={"code": "def fib(n): pass"})
    again = client.post(f"/api/specifications/{spec_id}/snapshots",
                        json={"code": "def fib(n): pass"}).json()
    assert again["saved"] is False  # unchanged code doesn't pile up rows

    latest = client.get(f"/api/specifications/{spec_id}/snapshots/latest").json()
    assert latest["content"] == "def fib(n): pass"
