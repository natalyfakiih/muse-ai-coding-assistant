"""Reflection-phase diagnosis: at submission the tutor asks questions that help
the student find the cause, without handing it over."""
from __future__ import annotations

import re

from tests.conftest import BUGGY_FIB, lock_specification


def submit(client, student_id, assignment_id):
    spec_id = lock_specification(client, student_id, assignment_id)
    result = client.post(f"/api/specifications/{spec_id}/submit",
                         json={"code": BUGGY_FIB}).json()
    return spec_id, result


def test_every_discrepancy_arrives_with_a_diagnostic_question(seeded):
    client, student_id, assignment_id = seeded
    _, result = submit(client, student_id, assignment_id)

    assert result["diff_results"]
    for diff in result["diff_results"]:
        assert diff["diagnostic_question"], "the student should not face a blank card"
        assert diff["diagnostic_question"].strip().endswith("?")


def test_the_question_does_not_hand_over_the_answer(seeded):
    """The bug is range(n + 1). A question naming the range, the +1, or the fix
    makes the explanation the student writes afterwards worthless."""
    client, student_id, assignment_id = seeded
    _, result = submit(client, student_id, assignment_id)

    for diff in result["diff_results"]:
        question = diff["diagnostic_question"].lower()
        for giveaway in ("range(", "n + 1", "n+1", "off by one", "off-by-one",
                         "change ", "should be ", "instead of"):
            assert giveaway not in question, f"gives it away: {diff['diagnostic_question']!r}"


def test_replying_gets_another_question_never_a_fix(seeded):
    client, student_id, assignment_id = seeded
    _, result = submit(client, student_id, assignment_id)
    diff_id = result["diff_results"][0]["id"]

    reply = client.post(f"/api/diff-results/{diff_id}/discuss",
                        json={"explanation": "I checked and it has 4 items"}).json()

    assert reply["question"].strip().endswith("?")
    assert reply["round"] == 1


def test_the_thread_goes_narrower_rather_than_repeating(seeded):
    """Regression: the opening question lives on the DiffResult rather than in
    the interaction log, so the first reply used to get it straight back."""
    client, student_id, assignment_id = seeded
    _, result = submit(client, student_id, assignment_id)
    diff = result["diff_results"][0]
    opening = diff["diagnostic_question"]

    asked = [opening]
    for message in ["not sure", "still not sure", "I really don't see it"]:
        asked.append(client.post(f"/api/diff-results/{diff['id']}/discuss",
                                 json={"explanation": message}).json()["question"])

    assert asked[1] != opening, "the first reply must not repeat the opening question"
    assert len(set(asked)) >= 3, "each round should go narrower"


def test_the_diagnosis_is_logged_against_the_discrepancy(seeded):
    client, student_id, assignment_id = seeded
    spec_id, result = submit(client, student_id, assignment_id)
    diff_id = result["diff_results"][0]["id"]

    client.post(f"/api/diff-results/{diff_id}/discuss",
                json={"explanation": "the loop runs one extra time"})

    thread = client.get(f"/api/diff-results/{diff_id}/thread").json()["thread"]
    assert len(thread) == 1
    assert thread[0]["student_message"] == "the loop runs one extra time"

    # and it lands in the same interaction log the instructor reads
    history = client.get(f"/api/specifications/{spec_id}/interactions").json()
    modes = [row["response_mode"] for row in history["interactions"]]
    assert "diagnostic" in modes


def test_diagnosis_does_not_replace_the_required_explanation(seeded):
    """The friction stays: talking it through is not writing it down."""
    client, student_id, assignment_id = seeded
    spec_id, result = submit(client, student_id, assignment_id)
    diff_id = result["diff_results"][0]["id"]

    client.post(f"/api/diff-results/{diff_id}/discuss", json={"explanation": "I see it now"})

    refused = client.post(f"/api/specifications/{spec_id}/finalize")
    assert refused.status_code == 400


def test_an_empty_reply_is_refused(seeded):
    client, student_id, assignment_id = seeded
    _, result = submit(client, student_id, assignment_id)
    diff_id = result["diff_results"][0]["id"]

    response = client.post(f"/api/diff-results/{diff_id}/discuss", json={"explanation": "   "})
    assert response.status_code in (400, 422)
