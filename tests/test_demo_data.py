"""The demo generator — what the Instructor Dashboard is shown with.

An empty dashboard demos nothing, so `app.demo` writes a cohort's worth of
finished work. These tests hold it to the two properties that make it usable:
it costs nothing to run, and it produces something worth looking at.
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app import demo
from app.db import Base, SessionLocal, engine
from app.models import (
    CourseSettings,
    DiffResult,
    GateTurn,
    Interaction,
    Specification,
    Student,
)
from app.modules import dashboard, profiler


@pytest.fixture()
def demo_db(monkeypatch):
    """A fresh database with the demo data in it, and every LLM entry point
    booby-trapped: generating demo data must not cost an API call."""
    def explode(*args, **kwargs):  # pragma: no cover - only runs on failure
        raise AssertionError("app.demo made an LLM call; it must be pure data")

    for module in ("app.llm", "app.modules.spec_gate", "app.modules.reflection",
                   "app.modules.dashboard", "app.modules.tutor"):
        monkeypatch.setattr(f"{module}.generate_structured", explode, raising=False)

    Base.metadata.drop_all(bind=engine)
    demo.RNG.seed(20260916)
    demo.build()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_every_table_the_dashboard_reads_has_rows(demo_db):
    for model in (Specification, GateTurn, Interaction, DiffResult):
        count = demo_db.scalar(select(func.count()).select_from(model))
        assert count > 0, f"{model.__name__} is empty — a dashboard panel will be blank"


def test_the_live_demo_student_is_left_empty(demo_db):
    """Rim drives the student side live during the demo. Pre-filled history for
    her would collide with whatever the demo actually produces."""
    rim = demo_db.scalars(
        select(Student).where(Student.name == demo.LIVE_DEMO_STUDENT)
    ).first()
    assert rim is not None
    specs = demo_db.scalars(
        select(Specification).where(Specification.student_id == rim.id)
    ).all()
    assert specs == []


def test_the_class_view_has_a_headline_not_just_noise(demo_db):
    """`class_patterns` earns its place by showing the thing much of the class
    got wrong. Eight students failing eight unrelated items would render a
    table nobody can act on."""
    patterns = dashboard.class_patterns(demo_db, demo.COURSE_ID)

    assert patterns["students"] >= 8
    top = patterns["diff_patterns"][0]
    assert top["students"] >= 3
    assert top["share_of_class"] >= 0.3


def test_all_three_discrepancy_categories_are_represented(demo_db):
    totals = dashboard.class_patterns(demo_db, demo.COURSE_ID)["diff_category_totals"]
    assert set(totals) == {"drift", "revision", "bug"}, totals
    assert all(count > 0 for count in totals.values())


def test_a_revision_always_carries_the_evidence_that_justifies_it(demo_db):
    """"revision" is the one category the classifier may not assign without a
    quote from the interaction log. Demo rows that break that rule would be
    showing off a guarantee the system doesn't make."""
    revisions = demo_db.scalars(
        select(DiffResult).where(DiffResult.category == "revision")
    ).all()
    assert revisions

    for diff in revisions:
        assert diff.evidence, "a revision without evidence contradicts CLASSIFY_DIFF"
        said = {row.student_message for row in demo_db.scalars(
            select(Interaction).where(Interaction.specification_id == diff.specification_id)
        ).all()}
        assert diff.evidence in said, "the quote is not in this student's log"


def test_cohorts_actually_differ(demo_db):
    """The dashboard's job is to make A/B/C distinguishable. If the generator
    produces the same student eight times, it is testing nothing.

    Measured per ATTEMPT, not per student: cohort C reaches fewer assignments
    and abandons more of them, so their raw total can land below cohort B even
    though they ask far more while they are working. Comparing totals would be
    comparing how much work they took on, not how much help they needed.
    """
    per_cohort: dict[str, list[float]] = {}
    for student in demo_db.scalars(select(Student)).all():
        if student.name == demo.LIVE_DEMO_STUDENT:
            continue
        questions = demo_db.scalar(
            select(func.count()).select_from(Interaction)
            .where(Interaction.student_id == student.id)
        )
        attempts = demo_db.scalar(
            select(func.count(func.distinct(Specification.assignment_id)))
            .where(Specification.student_id == student.id)
        ) or 1
        per_cohort.setdefault(student.cohort, []).append(questions / attempts)

    means = {c: sum(v) / len(v) for c, v in per_cohort.items()}
    assert means["A"] < means["B"] < means["C"], means


def test_no_student_shows_specification_work_with_zero_questions(demo_db):
    """A row reading "15 versions, 0 questions" looks like missing data during a
    demo, whatever the story behind it."""
    for student in demo_db.scalars(select(Student)).all():
        if student.name == demo.LIVE_DEMO_STUDENT:
            continue
        versions = demo_db.scalar(
            select(func.count()).select_from(Specification)
            .where(Specification.student_id == student.id)
        )
        questions = demo_db.scalar(
            select(func.count()).select_from(Interaction)
            .where(Interaction.student_id == student.id)
        )
        assert not (versions and not questions), f"{student.name}: {versions} versions, 0 questions"


def test_the_dependency_profile_panel_can_actually_be_opened(demo_db):
    """It refuses an instructor read unless the course opted in, so a demo that
    leaves the default would show a permission error where a panel should be."""
    assert demo_db.get(CourseSettings, demo.COURSE_ID).dependency_profile_visibility == (
        "instructor_opt_in")

    student = demo_db.scalars(
        select(Student).where(Student.name != demo.LIVE_DEMO_STUDENT)
    ).first()
    profile = profiler.profile(demo_db, student.id, viewer="instructor")
    assert profile["total_questions"] > 0


def test_every_student_overview_renders(demo_db):
    for student in demo_db.scalars(select(Student)).all():
        overview = dashboard.student_overview(demo_db, demo.COURSE_ID, student.id)
        assert overview["student"]["name"] == student.name
        for attempt in overview["assignments"]:
            assert attempt["specification_versions"]
            assert attempt["assignment_prompt"]


def test_clear_removes_the_demo_and_leaves_the_course_standing(demo_db):
    demo.build(clear_only=True)
    session = SessionLocal()
    try:
        assert session.scalar(select(func.count()).select_from(Specification)) == 0
        assert session.scalar(select(func.count()).select_from(DiffResult)) == 0
        assert session.scalar(select(func.count()).select_from(GateTurn)) == 0
        # students and assignments survive, so the app still works afterwards
        assert session.scalar(select(func.count()).select_from(Student)) == 8
    finally:
        session.close()


def test_it_is_deterministic():
    """Two runs, same database. A demo you cannot screenshot twice is not one
    you can put in a report."""
    def snapshot() -> list[tuple]:
        Base.metadata.drop_all(bind=engine)
        demo.RNG.seed(20260916)
        demo.build()
        session = SessionLocal()
        try:
            return sorted(
                (d.spec_item_key, d.category, d.description)
                for d in session.scalars(select(DiffResult)).all()
            )
        finally:
            session.close()

    assert snapshot() == snapshot()
