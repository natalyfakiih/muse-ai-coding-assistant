"""Instructor Dashboard and the dependency profile's visibility rule."""
from __future__ import annotations

from tests.conftest import BUGGY_FIB, lock_specification


def full_run(client, student_id, assignment_id):
    spec_id = lock_specification(client, student_id, assignment_id)
    client.post(f"/api/specifications/{spec_id}/interactions",
                json={"message": "what does .append() return?", "code": ""})
    for message in ["why is my loop off?", "still off", "still stuck on my loop"]:
        client.post(f"/api/specifications/{spec_id}/interactions",
                    json={"message": message, "code": BUGGY_FIB})
    client.post(f"/api/specifications/{spec_id}/interactions/escape-hatch", json={})
    submission = client.post(f"/api/specifications/{spec_id}/submit",
                             json={"code": BUGGY_FIB}).json()
    for diff in submission["diff_results"]:
        client.post(f"/api/diff-results/{diff['id']}/explanation",
                    json={"explanation": "I looped one extra time with range(n + 1)."})
    return spec_id


def test_per_student_overview_carries_history_transcript_and_explanations(seeded):
    client, student_id, assignment_id = seeded
    full_run(client, student_id, assignment_id)

    overview = client.get(f"/api/courses/CS101/students/{student_id}/overview").json()
    assignment = overview["assignments"][0]

    assert len(assignment["specification_versions"]) >= 2
    assert assignment["interactions"]
    assert assignment["diff_results"]
    assert all(d["student_explanation"] for d in assignment["diff_results"])


def test_class_patterns_aggregate_without_an_ai_call(seeded):
    client, student_id, assignment_id = seeded
    full_run(client, student_id, assignment_id)

    before = client.get("/api/ops/llm-calls").json()["total_calls"]
    patterns = client.get("/api/courses/CS101/analytics/class-patterns").json()
    after = client.get("/api/ops/llm-calls").json()["total_calls"]

    assert after == before, "the class view must not make LLM calls at read time"
    assert patterns["students"] == 8
    assert patterns["diff_patterns"]
    assert patterns["escape_hatch_uses"] >= 1
    assert 0 < patterns["diff_patterns"][0]["share_of_class"] <= 1


def test_narrative_summary_is_one_call_on_demand(seeded):
    client, student_id, assignment_id = seeded
    full_run(client, student_id, assignment_id)

    before = client.get("/api/ops/llm-calls").json()["calls_by_task"].get("narrative_summary", 0)
    summary = client.get(f"/api/students/{student_id}/narrative-summary").json()
    after = client.get("/api/ops/llm-calls").json()["calls_by_task"]["narrative_summary"]

    assert summary["summary"]
    assert after == before + 1


def test_dependency_profile_is_student_facing_by_default(seeded):
    client, student_id, assignment_id = seeded
    full_run(client, student_id, assignment_id)

    profile = client.get(f"/api/students/{student_id}/dependency-profile").json()
    assert profile["visibility"] == "student_only"
    assert profile["problem_solving"] >= 3
    assert profile["factual"] >= 1
    assert profile["escape_hatch_uses"] == 1
    assert profile["assist_cost_total"] == 1


def test_instructor_cannot_read_the_profile_until_the_course_opts_in(seeded):
    """Section 3.5: a visibility flag in the data model, not a hidden div."""
    client, student_id, assignment_id = seeded
    full_run(client, student_id, assignment_id)

    blocked = client.get(f"/api/students/{student_id}/dependency-profile",
                         params={"viewer": "instructor"})
    assert blocked.status_code == 403

    client.put("/api/courses/CS101/settings/dependency-profile-visibility",
               json={"visibility": "instructor_opt_in"})

    allowed = client.get(f"/api/students/{student_id}/dependency-profile",
                         params={"viewer": "instructor"})
    assert allowed.status_code == 200
    assert allowed.json()["visibility"] == "instructor_opt_in"


def test_provider_log_records_every_call(seeded):
    client, student_id, assignment_id = seeded
    full_run(client, student_id, assignment_id)

    ops = client.get("/api/ops/llm-calls").json()
    assert ops["total_calls"] > 0
    assert set(ops["calls_by_task"]) >= {
        "extract_fields", "next_gap", "classify_question", "socratic_question",
        "direct_hint", "classify_diff",
    }
    assert "fallback_rate" in ops
