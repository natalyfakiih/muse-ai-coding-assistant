"""Specification Gate — the rubric loop and the anti-lock-in override."""
from __future__ import annotations

from app.config import settings
from app.models import Specification


def post(client, assignment_id, student_id, message):
    return client.post(
        f"/api/assignments/{assignment_id}/specification",
        json={"student_id": student_id, "message": message},
    ).json()


def test_gate_asks_one_question_at_a_time_about_an_unmet_rubric_item(seeded):
    client, student_id, assignment_id = seeded
    result = post(client, assignment_id, student_id, "I want to print fibonacci numbers.")

    assert result["locked"] is False
    assert result["question"]
    assert result["gap"] is not None
    assert result["asks_on_gap"] == 1


def test_every_clarification_round_creates_a_new_version(seeded, db):
    client, student_id, assignment_id = seeded
    post(client, assignment_id, student_id, "A function for fibonacci numbers.")
    post(client, assignment_id, student_id, "It takes an int n and returns a list.")

    versions = db.query(Specification).filter(
        Specification.student_id == student_id,
        Specification.assignment_id == assignment_id,
    ).order_by(Specification.version).all()

    assert [v.version for v in versions] == list(range(1, len(versions) + 1))
    assert len(versions) >= 3  # v1 empty, then one per substantive message
    assert versions[0].content["goal"] == ""  # drift can be measured against v1


def test_gate_unlocks_once_every_rubric_item_is_answered(seeded):
    client, student_id, assignment_id = seeded
    for message in [
        "A function that returns the first n fibonacci numbers.",
        "It takes an int n and returns a list of ints. n=3 -> [0, 1, 1]",
        "For n = 0 it should return an empty list.",
        "Negative n should also return an empty list.",
    ]:
        result = post(client, assignment_id, student_id, message)
    assert result["locked"] is True
    assert result["status"] == "locked"


def test_a_structural_gap_cannot_be_waived(seeded):
    """"My answer stands" on "give me an example" records nothing and leaves the
    Reflection Engine with nothing to run, so the gate refuses it."""
    client, student_id, assignment_id = seeded
    post(client, assignment_id, student_id, "fibonacci numbers")

    result = client.post(
        f"/api/assignments/{assignment_id}/specification/lock",
        json={"student_id": student_id},
    ).json()

    assert result["locked"] is False
    assert "can't be waived" in result["message"]


def test_the_gate_never_unlocks_without_at_least_one_example(seeded):
    """Regression: the model used to be able to wave a near-empty specification
    through, which made phase 3 a silent no-op."""
    client, student_id, assignment_id = seeded

    result = post(client, assignment_id, student_id, "fibonacci numbers, takes an int n")
    for _ in range(8):
        if result["locked"]:
            break
        client.post(f"/api/assignments/{assignment_id}/specification/lock",
                    json={"student_id": student_id})
        result = post(client, assignment_id, student_id, "same answer as before")

    assert result["locked"] is False
    assert not result["content"]["examples"]


def test_every_rubric_item_is_asked_before_the_editor_unlocks(seeded):
    """Rim's report: the gate was ticking off 'behavior when n = 0' because the
    item id contains the substring 'input' and an input had been named."""
    client, student_id, assignment_id = seeded
    asked = set()
    for message in [
        "a function that returns the first n fibonacci numbers",
        "takes an int n and returns a list of ints",
        "n=3 -> [0, 1, 1]",
        "for n = 0 it should return an empty list",
        "negative n should also return an empty list",
    ]:
        result = post(client, assignment_id, student_id, message)
        if result["gap"]:
            asked.add(result["gap"]["id"])
        if result["locked"]:
            break

    assert result["locked"] is True
    assert {"empty_input", "negative_input"} <= asked, (
        f"the conditional items must be asked, not inferred; asked={asked}")


def test_confirming_one_semantic_gap_does_not_unlock_everything(seeded):
    client, student_id, assignment_id = seeded
    post(client, assignment_id, student_id,
         "a function returning the first n fibonacci numbers, takes int n, "
         "returns a list of ints, n=3 -> [0, 1, 1]")
    second = post(client, assignment_id, student_id, "dunno")
    assert second["gap"]["id"] == "empty_input"

    override = client.post(
        f"/api/assignments/{assignment_id}/specification/lock",
        json={"student_id": student_id, "gap_id": "empty_input"},
    ).json()

    assert override["locked"] is False
    assert (override["gap"] or {}).get("id") == "negative_input"


def test_overriding_every_semantic_gap_eventually_unlocks(seeded):
    """The override must still be an escape valve, not a trap."""
    client, student_id, assignment_id = seeded
    result = post(client, assignment_id, student_id,
                  "a function returning the first n fibonacci numbers, takes int n, "
                  "returns a list of ints, n=3 -> [0, 1, 1]")

    for _ in range(12):
        if result["locked"]:
            break
        if result.get("can_force_lock"):
            result = client.post(
                f"/api/assignments/{assignment_id}/specification/lock",
                json={"student_id": student_id,
                      "gap_id": (result.get("gap") or {}).get("id")},
            ).json()
        else:
            result = post(client, assignment_id, student_id, "same answer as before")

    assert result["locked"] is True


def test_reading_the_specification_returns_rubric_and_history(seeded):
    client, student_id, assignment_id = seeded
    post(client, assignment_id, student_id, "fibonacci numbers up to n")

    data = client.get(
        f"/api/assignments/{assignment_id}/specification",
        params={"student_id": student_id},
    ).json()

    assert data["rubric"], "the gate's completeness rule must be visible to the client"
    assert data["version_history"]
    assert set(data["content"]) == {
        "goal", "inputs", "outputs", "edge_cases", "examples", "explicit_exclusions",
    }


def test_unstarted_specification_reads_as_not_started(seeded):
    client, _, assignment_id = seeded
    students = client.get("/api/students").json()
    data = client.get(
        f"/api/assignments/{assignment_id}/specification",
        params={"student_id": students[-1]["id"]},
    ).json()
    assert data["status"] == "not_started"
    assert data["locked"] is False


def test_version_history_carries_content_for_the_student_view(seeded):
    """Going back to phase 1 after locking shows how the specification evolved,
    so the history has to include each version's content, not just its id."""
    client, student_id, assignment_id = seeded
    post(client, assignment_id, student_id, "a fibonacci function")
    post(client, assignment_id, student_id, "takes an int n and returns a list of ints")

    data = client.get(f"/api/assignments/{assignment_id}/specification",
                      params={"student_id": student_id}).json()

    assert len(data["version_history"]) >= 2
    assert all("content" in v for v in data["version_history"])
    assert data["version_history"][0]["content"]["goal"] == ""
    assert data["version_history"][-1]["content"]["goal"]
