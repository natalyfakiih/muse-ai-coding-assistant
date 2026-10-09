"""Session statistics — arithmetic over existing rows, no AI calls."""
from __future__ import annotations

from tests.conftest import BUGGY_FIB, lock_specification
from tests.test_spec_gate import post


def full_session(client, student_id, assignment_id):
    spec_id = lock_specification(client, student_id, assignment_id)
    client.post(f"/api/specifications/{spec_id}/interactions",
                json={"message": "what does .append() return?", "code": ""})
    for message in ["why is my loop off?", "still off", "still stuck on my loop"]:
        client.post(f"/api/specifications/{spec_id}/interactions",
                    json={"message": message, "code": BUGGY_FIB})
    client.post(f"/api/specifications/{spec_id}/interactions/escape-hatch", json={})
    submission = client.post(f"/api/specifications/{spec_id}/submit",
                             json={"code": BUGGY_FIB}).json()
    return spec_id, submission


def test_stats_cost_nothing(seeded):
    """It's a summary of what already happened — an LLM call here would be
    money spent to recount rows we already have."""
    client, student_id, assignment_id = seeded
    spec_id, _ = full_session(client, student_id, assignment_id)

    before = client.get("/api/ops/llm-calls").json()["total_calls"]
    client.get(f"/api/specifications/{spec_id}/session-stats")
    after = client.get("/api/ops/llm-calls").json()["total_calls"]
    assert after == before


def test_phase1_counts_versions_questions_and_retries(seeded):
    client, student_id, assignment_id = seeded
    spec_id, _ = full_session(client, student_id, assignment_id)

    one = client.get(f"/api/specifications/{spec_id}/session-stats").json()["phase1"]
    assert one["versions"] >= 2
    assert one["questions_asked"] >= 1
    assert one["asked_again"] >= 0
    assert one["seconds"] is not None


def test_a_gap_asked_twice_counts_as_one_retry(seeded):
    """'Asked again' is the honest retry measure — the raw ask total counts the
    first, perfectly reasonable question too."""
    client, student_id, assignment_id = seeded
    post(client, assignment_id, student_id, "fibonacci")
    post(client, assignment_id, student_id, "dunno")
    spec = client.get(f"/api/assignments/{assignment_id}/specification",
                      params={"student_id": student_id}).json()

    one = client.get(f"/api/specifications/{spec['specification_id']}/session-stats"
                     ).json()["phase1"]
    assert one["questions_asked"] == 2
    assert one["asked_again"] == 1


def test_phase2_splits_factual_from_problem_solving(seeded):
    client, student_id, assignment_id = seeded
    spec_id, _ = full_session(client, student_id, assignment_id)

    two = client.get(f"/api/specifications/{spec_id}/session-stats").json()["phase2"]
    assert two["questions_asked"] >= 4
    assert two["factual"] >= 1
    assert two["problem_solving"] >= 3
    assert two["direct_hints"] == 1
    assert two["longest_thread"] >= 3
    assert two["code_saves"] >= 1


def test_phase3_counts_failed_cases_and_submissions(seeded):
    client, student_id, assignment_id = seeded
    spec_id, submission = full_session(client, student_id, assignment_id)

    three = client.get(f"/api/specifications/{spec_id}/session-stats").json()["phase3"]
    assert three["submissions"] == 1
    assert three["cases_failed"] == len(submission["diff_results"])
    assert sum(three["by_category"].values()) == three["discrepancies"]
    assert three["failed_cases"], "the student should see which cases failed"
    assert three["explanations_written"] == 0

    diff_id = submission["diff_results"][0]["id"]
    client.post(f"/api/diff-results/{diff_id}/explanation",
                json={"explanation": "I looped one extra time with range(n + 1)."})
    three = client.get(f"/api/specifications/{spec_id}/session-stats").json()["phase3"]
    assert three["explanations_written"] == 1


def test_resubmitting_shows_up_as_a_second_submission(seeded):
    client, student_id, assignment_id = seeded
    spec_id, _ = full_session(client, student_id, assignment_id)
    client.post(f"/api/specifications/{spec_id}/submit", json={"code": BUGGY_FIB})

    three = client.get(f"/api/specifications/{spec_id}/session-stats").json()["phase3"]
    assert three["submissions"] == 2


# ------------------------------------------------------- transcript
def test_the_transcript_keeps_the_gate_conversation(seeded):
    """Regression: gate dialogue was never stored, only its outcome — so the
    questions and the student's answers were gone the moment the page reloaded."""
    client, student_id, assignment_id = seeded
    post(client, assignment_id, student_id, "a function for fibonacci numbers")
    post(client, assignment_id, student_id, "it takes an int n")
    spec = client.get(f"/api/assignments/{assignment_id}/specification",
                      params={"student_id": student_id}).json()

    entries = client.get(f"/api/specifications/{spec['specification_id']}/session-stats"
                         ).json()["transcript"]
    phase1 = [e for e in entries if e["phase"] == 1]
    assert len(phase1) >= 2
    assert phase1[0]["student"] == "a function for fibonacci numbers"
    assert phase1[0]["muse"], "the question MUSE asked has to be there too"


def test_the_transcript_covers_all_three_phases_in_order(seeded):
    client, student_id, assignment_id = seeded
    spec_id, submission = full_session(client, student_id, assignment_id)
    client.post(f"/api/diff-results/{submission['diff_results'][0]['id']}/discuss",
                json={"explanation": "the loop runs one extra time"})

    entries = client.get(f"/api/specifications/{spec_id}/session-stats").json()["transcript"]
    phases = {e["phase"] for e in entries}
    assert phases == {1, 2, 3}
    timestamps = [e["at"] for e in entries if e["at"]]
    assert timestamps == sorted(timestamps), "merged from two tables, so order matters"


def test_dont_know_and_overrides_are_visible_in_the_transcript(seeded):
    client, student_id, assignment_id = seeded
    post(client, assignment_id, student_id, "fibonacci numbers")
    client.post(f"/api/assignments/{assignment_id}/specification/dont-know",
                json={"student_id": student_id})
    spec = client.get(f"/api/assignments/{assignment_id}/specification",
                      params={"student_id": student_id}).json()

    entries = client.get(f"/api/specifications/{spec['specification_id']}/session-stats"
                         ).json()["transcript"]
    assert any(e["tag"] == "dont_know" for e in entries)


# ------------------------------------------------------- the even/odd assignment
def test_the_even_odd_assignment_runs_end_to_end(client):
    """Its rubric turns on a genuine design decision — true/false or the words —
    so it exercises the type gap, two condition gaps and the example gap."""
    assignment = [a for a in client.get("/api/assignments").json()
                  if a["entry_point"] == "even_or_odd"][0]
    aid, sid = assignment["id"], 1

    assert [i["id"] for i in assignment["rubric_items"]] == [
        "goal", "inputs", "outputs", "zero", "negative", "examples"]

    result = {}
    for message in ["a function that says whether a whole number is even or odd",
                    "it takes an int n and returns a string",
                    'for example n=4 -> "even"',
                    "when the number is 0 it returns even",
                    "for negative numbers it works the same way, -3 gives odd"]:
        result = client.post(f"/api/assignments/{aid}/specification",
                             json={"student_id": sid, "message": message}).json()
        if result["locked"]:
            break
    assert result["locked"] is True

    spec_id = result["specification_id"]
    buggy = ("def even_or_odd(n):\n    if n == 0:\n        return 'odd'\n"
             "    return 'even' if n % 2 == 0 else 'odd'\n")
    good = "def even_or_odd(n):\n    return 'even' if n % 2 == 0 else 'odd'\n"

    failed = client.post(f"/api/specifications/{spec_id}/submit",
                         json={"code": buggy}).json()
    assert failed["diff_results"], "declaring 0 is even and returning odd must surface"

    passed = client.post(f"/api/specifications/{spec_id}/submit",
                         json={"code": good}).json()
    assert passed["diff_results"] == []


def test_seeding_adds_a_new_assignment_without_wiping_anything(db):
    """Adding an assignment shouldn't cost everyone their work."""
    from app.models import Assignment, Specification
    import app.seed as seed_module

    db.add(Specification(assignment_id=1, student_id=1, version=1,
                         content={}, status="locked"))
    db.commit()
    before = db.query(Specification).count()

    seed_module.seed()          # no --reset

    assert db.query(Specification).count() == before, "existing work survived"
    entry_points = {(a.rubric or {}).get("entry_point") for a in db.query(Assignment).all()}
    assert entry_points == {"fib", "dedupe", "even_or_odd"}
    assert db.query(Assignment).count() == 3, "and nothing was duplicated"
