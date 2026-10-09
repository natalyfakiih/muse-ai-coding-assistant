"""The insights engine — the numbers and the label beside a student's name.

Two things make this module worth testing hard. It produces a judgement that an
instructor will act on, and it decides what a student is allowed to see about
themselves. Both have to be right for reasons that are not "the test suite is
green".
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app import demo
from app.db import Base, SessionLocal, engine
from app.models import (
    CodeSnapshot,
    DiffResult,
    Interaction,
    Specification,
    Student,
    SubmissionAttempt,
)
from app.modules import insights


@pytest.fixture()
def seeded(monkeypatch):
    def explode(*args, **kwargs):  # pragma: no cover - only on failure
        raise AssertionError("insights made an LLM call; it must be pure arithmetic")

    for module in ("app.llm", "app.modules.insights", "app.modules.dashboard"):
        monkeypatch.setattr(f"{module}.generate_structured", explode, raising=False)

    Base.metadata.drop_all(bind=engine)
    demo.RNG.seed(20260916)
    demo.build()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# --------------------------------------------------------------------------
# the tier
# --------------------------------------------------------------------------
def test_the_tier_is_a_sum_of_its_signals():
    """Nothing is hidden in the scoring: the tier is the signal points added up
    and compared to two published thresholds."""
    assert insights.tier_for([{"points": 0}]) == ("on_track", 0)
    assert insights.tier_for([{"points": 2}]) == ("on_track", 2)
    assert insights.tier_for([{"points": 2}, {"points": 1}]) == ("needs_attention", 3)
    assert insights.tier_for([{"points": 2}, {"points": 2}, {"points": 1}]) == ("at_risk", 5)


def test_a_student_with_no_work_is_not_on_track(seeded):
    """Zero signals fire for someone who has done nothing, which would score 0
    and read as the best tier. "Not started" is a different fact."""
    rim = seeded.scalars(select(Student).where(Student.name == demo.LIVE_DEMO_STUDENT)).first()
    result = insights.student_insights(seeded, rim.id, viewer="instructor")

    assert result["status"]["tier"] == "not_started"
    assert result["status"]["signals"] == []


def test_every_flagged_signal_carries_its_number_and_its_rows(seeded):
    """A label an instructor cannot interrogate is worse than no label, because
    they will act on it anyway."""
    data = insights.class_insights(seeded, demo.COURSE_ID)
    flagged = [r for r in data["roster"] if r["tier"] in ("needs_attention", "at_risk")]
    assert flagged, "demo data should produce at least one flagged student"

    for row in flagged:
        detail = insights.student_insights(seeded, row["student_id"], viewer="instructor")
        for signal in detail["status"]["signals"]:
            if signal["points"] > 0:
                assert signal["value"], f"{signal['id']} has no number"
                assert signal["proof"], f"{signal['id']} has no rows behind it"


def test_the_cohort_spans_every_tier(seeded):
    """The demo exists so the dashboard can be seen working. A roster that is
    entirely "on track" demonstrates nothing."""
    distribution = insights.class_insights(seeded, demo.COURSE_ID)["distribution"]
    assert distribution["at_risk"] >= 1
    assert distribution["needs_attention"] >= 1
    assert distribution["on_track"] >= 1


# --------------------------------------------------------------------------
# what a student is allowed to see
# --------------------------------------------------------------------------
def test_the_student_viewer_never_receives_the_tier(seeded):
    """Not hidden in the page — absent from the payload. The student's browser
    never holds the label at all."""
    student = seeded.scalars(
        select(Student).where(Student.name != demo.LIVE_DEMO_STUDENT)).first()

    mine = insights.student_insights(seeded, student.id, viewer="student")
    theirs = insights.student_insights(seeded, student.id, viewer="instructor")

    assert "status" not in mine
    assert "status" in theirs
    # Everything useful is still there: the student loses the label, not the data.
    for key in ("timings", "totals", "areas", "strengths", "weaknesses", "plan"):
        assert mine[key] == theirs[key]


# --------------------------------------------------------------------------
# timing
# --------------------------------------------------------------------------
def test_submitting_records_an_attempt_with_the_gap_since_the_last_one(seeded):
    """`submit()` replaces the DiffResult rows each time, so without this table
    a fourth submission is indistinguishable from a first."""
    attempts = seeded.scalars(select(SubmissionAttempt)).all()
    assert attempts

    retries = [a for a in attempts if a.attempt_number > 1]
    assert retries, "demo data should contain at least one retry"
    assert all(a.seconds_since_previous is not None for a in retries)
    assert all(a.seconds_since_previous >= 0 for a in retries)
    # The first attempt has nothing to measure from, and must say so rather
    # than reporting a zero that would drag every average down.
    assert all(a.seconds_since_previous is None
               for a in attempts if a.attempt_number == 1)


def test_time_is_measured_between_real_rows(seeded):
    student = seeded.scalars(
        select(Student).where(Student.name != demo.LIVE_DEMO_STUDENT)).first()
    result = insights.student_insights(seeded, student.id, viewer="instructor")

    for timing in result["timings"]:
        for key in ("specification_seconds", "coding_seconds", "total_seconds"):
            value = timing[key]
            assert value is None or value >= 0, f"{key} went backwards"
        if timing["specification_seconds"] and timing["total_seconds"]:
            assert timing["specification_seconds"] <= timing["total_seconds"]


def test_the_submissions_norm_is_per_assignment_not_per_student(seeded):
    """The signal it is compared against counts submissions on ONE assignment.
    Totalling them across three made every student look diligent by comparison.
    """
    norms = insights.class_norms_for(seeded, demo.COURSE_ID)
    per_assignment_counts = {}
    for attempt in seeded.scalars(select(SubmissionAttempt)).all():
        per_assignment_counts[attempt.specification_id] = \
            per_assignment_counts.get(attempt.specification_id, 0) + 1

    biggest = max(per_assignment_counts.values())
    assert norms["submissions"] <= biggest, (
        "the class norm exceeds the largest single assignment, so it is "
        "summing across assignments again")


# --------------------------------------------------------------------------
# strengths, weaknesses, plan
# --------------------------------------------------------------------------
def test_an_area_is_only_scored_when_there_is_evidence(seeded):
    student = seeded.scalars(
        select(Student).where(Student.name != demo.LIVE_DEMO_STUDENT)).first()
    areas = insights.student_insights(seeded, student.id)["areas"]

    for name, value in areas.items():
        assert name in insights.AREAS, f"unknown area {name}"
        assert 0.0 <= value["score"] <= 1.0
        assert value["evidence"], f"{name} was scored with nothing behind it"


def test_the_plan_addresses_the_weakest_areas_and_says_why(seeded):
    data = insights.class_insights(seeded, demo.COURSE_ID)
    weakest = next(r for r in data["roster"] if r["tier"] == "at_risk")
    detail = insights.student_insights(seeded, weakest["student_id"], viewer="instructor")

    assert detail["plan"], "an at-risk student should get a plan"
    planned = {item["area"] for item in detail["plan"]}
    weak = {item["area"] for item in detail["weaknesses"]}
    assert planned <= weak, "the plan addresses an area that is not a weakness"

    for item in detail["plan"]:
        assert item["steps"], "a plan step with no steps is advice, not a plan"
        assert item["because"], "a plan item must say what it is responding to"


def test_the_plan_routes_through_a_strength_when_one_fits():
    """The point of naming strengths is to use them. A plan that ignores them is
    a generic worksheet."""
    weaknesses = [{"area": "edge_cases", "evidence": ["3 drift rows on edge cases"]}]
    strengths = [{"area": "specification", "score": 1.0}]

    plan = insights._build_plan(weaknesses, strengths)

    assert len(plan) == 1
    assert plan[0]["leaning_on"], "a matching strength was available and unused"
    assert "specification" in plan[0]["leaning_on"].lower() or \
           "goal" in plan[0]["leaning_on"].lower()


def test_a_weakness_with_no_matching_strength_still_gets_a_plan():
    plan = insights._build_plan([{"area": "types", "evidence": ["x"]}], [])
    assert len(plan) == 1
    assert plan[0]["leaning_on"] is None
    assert plan[0]["steps"]


def test_areas_are_matched_from_the_specification_item_key():
    assert insights.area_for("edge_case:empty list returns []") == "edge_cases"
    assert insights.area_for("edge_case:negative n returns []") == "edge_cases"
    assert insights.area_for("outputs") == "types"
    assert insights.area_for("examples[0]") == "examples"
    assert insights.area_for("goal") == "specification"
    assert insights.area_for(None) is None
    assert insights.area_for("something unmapped") is None


# --------------------------------------------------------------------------
# formatting
# --------------------------------------------------------------------------
@pytest.mark.parametrize("seconds,expected", [
    (None, "—"), (0, "—"), (45, "45s"), (600, "10 min"), (7200, "2.0 h"),
])
def test_durations_read_as_a_human_would_say_them(seconds, expected):
    assert insights._human(seconds) == expected
