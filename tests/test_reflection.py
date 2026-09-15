"""Reflection Engine — sandbox, behavioral diff, and the mandatory explanation."""
from __future__ import annotations

import pytest

from app.modules import sandbox
from tests.conftest import BUGGY_FIB, GOOD_FIB, lock_specification


# ---------------------------------------------------------------- sandbox
def test_call_arguments_are_parsed_from_student_written_examples():
    assert sandbox.parse_call_args("n=3") == ([], {"n": 3})
    assert sandbox.parse_call_args("items=[1, 2], k=2") == ([], {"items": [1, 2], "k": 2})
    assert sandbox.parse_call_args("3") == ([3], {})
    assert sandbox.parse_call_args("") == ([], {})


def test_expected_output_comparison_tolerates_whitespace_and_formatting():
    assert sandbox.matches("[0, 1, 1]", "[0,1,1]") is True
    assert sandbox.matches("[0, 1, 1]", "[0, 1]") is False
    assert sandbox.matches("1", "True") is False  # 1 == True in Python; not here


def test_sandbox_runs_code_and_returns_the_value():
    result = sandbox.run_case(GOOD_FIB, "fib", "n=3")
    assert result.ok and result.value_repr == "[0, 1, 1]" and result.value_type == "list"


def test_sandbox_reports_an_exception_instead_of_raising():
    result = sandbox.run_case("def f(n):\n    return 1 / 0", "f", "n=1")
    assert result.ok is False and "ZeroDivisionError" in result.error


def test_sandbox_reports_a_missing_entry_point():
    result = sandbox.run_case("def other(n): return n", "fib", "n=1")
    assert result.ok is False and "fib" in result.error


@pytest.mark.skipif(not sandbox.POSIX, reason="resource limits are POSIX-only")
def test_sandbox_stops_an_infinite_loop():
    result = sandbox.run_case("def f(n):\n    while True:\n        pass", "f", "n=1")
    # Either guard may win the race: the CPU rlimit (SIGXCPU) or the wall clock.
    assert result.ok is False
    assert result.timed_out or "limit" in (result.error or "").lower()


@pytest.mark.skipif(not sandbox.POSIX, reason="resource limits are POSIX-only")
def test_sandbox_stops_a_runaway_allocation():
    result = sandbox.run_case("def f(n):\n    return [0] * 10**9", "f", "n=1")
    assert result.ok is False


# ------------------------------------------------------------- submission
def test_submission_flags_code_that_diverges_from_the_specification(seeded):
    client, student_id, assignment_id = seeded
    spec_id = lock_specification(client, student_id, assignment_id)

    result = client.post(f"/api/specifications/{spec_id}/submit",
                         json={"code": BUGGY_FIB}).json()

    assert result["cases_run"] >= 1
    assert result["diff_results"], "an off-by-one against a declared example must surface"
    assert all(d["category"] in ("drift", "revision", "bug") for d in result["diff_results"])
    assert result["can_finalize"] is False


def test_correct_code_produces_no_discrepancies(seeded):
    client, student_id, assignment_id = seeded
    spec_id = lock_specification(client, student_id, assignment_id)

    result = client.post(f"/api/specifications/{spec_id}/submit",
                         json={"code": GOOD_FIB}).json()

    assert result["diff_results"] == []
    assert result["can_finalize"] is True


def test_declared_edge_cases_are_executed_not_just_the_happy_path(seeded):
    """The 'never handled empty input' case is the whole point of Section 3.3;
    checking only the declared examples would miss it."""
    client, student_id, assignment_id = seeded
    spec_id = lock_specification(client, student_id, assignment_id)

    result = client.post(f"/api/specifications/{spec_id}/submit",
                         json={"code": BUGGY_FIB}).json()

    sources = [case["source"] for case in result["results"]]
    assert any("edge case" in source for source in sources)


def test_submission_is_refused_until_every_discrepancy_is_explained(seeded):
    client, student_id, assignment_id = seeded
    spec_id = lock_specification(client, student_id, assignment_id)
    submission = client.post(f"/api/specifications/{spec_id}/submit",
                             json={"code": BUGGY_FIB}).json()

    refused = client.post(f"/api/specifications/{spec_id}/finalize")
    assert refused.status_code == 400

    for diff in submission["diff_results"]:
        client.post(f"/api/diff-results/{diff['id']}/explanation",
                    json={"explanation": "I used range(n + 1) so it runs one extra time."})

    accepted = client.post(f"/api/specifications/{spec_id}/finalize").json()
    assert accepted["can_finalize"] is True
    assert accepted["finalized_at"]


def test_a_one_word_explanation_is_rejected(seeded):
    """The friction is deliberate — 'oops' must not clear the gate."""
    client, student_id, assignment_id = seeded
    spec_id = lock_specification(client, student_id, assignment_id)
    submission = client.post(f"/api/specifications/{spec_id}/submit",
                             json={"code": BUGGY_FIB}).json()

    diff_id = submission["diff_results"][0]["id"]
    response = client.post(f"/api/diff-results/{diff_id}/explanation",
                           json={"explanation": "oops"})
    assert response.status_code == 400


def test_resubmitting_replaces_the_previous_verdict(seeded):
    client, student_id, assignment_id = seeded
    spec_id = lock_specification(client, student_id, assignment_id)

    client.post(f"/api/specifications/{spec_id}/submit", json={"code": BUGGY_FIB})
    second = client.post(f"/api/specifications/{spec_id}/submit",
                         json={"code": GOOD_FIB}).json()

    assert second["diff_results"] == []
    status = client.get(f"/api/specifications/{spec_id}/submission-status").json()
    assert status["diff_results_total"] == 0


def test_submission_before_locking_is_refused(seeded):
    client, student_id, assignment_id = seeded
    client.post(f"/api/assignments/{assignment_id}/specification",
                json={"student_id": student_id, "message": "fibonacci"})
    spec = client.get(f"/api/assignments/{assignment_id}/specification",
                      params={"student_id": student_id}).json()

    response = client.post(f"/api/specifications/{spec['specification_id']}/submit",
                           json={"code": GOOD_FIB})
    assert response.status_code == 400


def test_submitting_against_an_exampleless_specification_is_refused(client, db):
    """The gate no longer lets a student reach this state, but the Reflection
    Engine must still refuse it: running zero cases and reporting a clean pass
    is a submission that was never actually checked.
    """
    from app.models import CodeSnapshot, Specification

    spec = Specification(
        assignment_id=1, student_id=1, version=1, status="locked",
        content={"goal": "fibonacci", "inputs": [], "outputs": [],
                 "edge_cases": [], "examples": [], "explicit_exclusions": []},
    )
    db.add(spec)
    db.flush()
    db.add(CodeSnapshot(specification_id=spec.id, content=GOOD_FIB))
    db.commit()

    response = client.post(f"/api/specifications/{spec.id}/submit", json={"code": GOOD_FIB})
    assert response.status_code == 400
    assert "nothing to run your code against" in response.json()["detail"]


def test_submission_status_carries_the_discrepancies_for_a_returning_student(seeded):
    """Phase 3 has to be reachable again after a reload, so the status endpoint
    returns the discrepancies themselves, not just a count."""
    client, student_id, assignment_id = seeded
    spec_id = lock_specification(client, student_id, assignment_id)
    client.post(f"/api/specifications/{spec_id}/submit", json={"code": BUGGY_FIB})

    status = client.get(f"/api/specifications/{spec_id}/submission-status").json()

    assert status["submitted"] is True
    assert status["diff_results"], "a returning student must see their results"
    assert status["diff_results"][0]["category"] in ("drift", "revision", "bug")
    assert status["diff_results"][0]["test_detail"]
