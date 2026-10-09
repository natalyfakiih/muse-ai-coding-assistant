"""Data model — Section 2 of the spec.

Six entities from the spec, plus two operational tables:
  * llm_calls        — per-call provider/latency/outcome log, so you can see
                       how often Mistral's RPM cap pushes you onto Groq.
  * course_settings  — dependency-profile visibility as a data-model flag,
                       not a UI toggle (Section 3.5 design note).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, JSONColumn


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Core entities
# --------------------------------------------------------------------------
class Student(Base):
    __tablename__ = "students"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    course_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    cohort: Mapped[str | None] = mapped_column(String(100))

    specifications: Mapped[list["Specification"]] = relationship(back_populates="student")


class Subject(Base):
    """A subject an instructor teaches, and the thing that decides how phase 3
    checks work.

    `checker` is the one field with teeth: it picks which checker the Reflection
    Engine runs. "python" runs the student's code in the sandbox and diffs its
    behaviour against the locked specification; "math" compares a declared
    answer symbolically. Phases 1 and 2 do not care which subject this is —
    stating your plan before you act, and getting questions instead of answers,
    is not a programming idea.
    """

    __tablename__ = "subjects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    checker: Mapped[str] = mapped_column(String(30), nullable=False, default="python")
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    materials: Mapped[list["SubjectMaterial"]] = relationship(
        back_populates="subject", cascade="all, delete-orphan",
        order_by="SubjectMaterial.position",
    )


class SubjectMaterial(Base):
    """What the instructor teaches from, in their own words.

    Ollie explains from these rows and nothing else. Storing the material as
    plain text rather than a file keeps retrieval simple and keeps the
    instructor able to edit a sentence without re-uploading a document; a file
    upload can sit on top of this later and write into the same field.
    """

    __tablename__ = "subject_materials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id"), index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    subject: Mapped[Subject] = relationship(back_populates="materials")


class Assignment(Base):
    __tablename__ = "assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    # rubric shape:
    #   {"entry_point": "fib",
    #    "items": [{"id": "empty_input", "text": "must state behavior on
    #               empty input", "priority": 1}, ...]}
    # The Specification Gate walks `items`; the Reflection Engine calls
    # `entry_point` when running the behavioral diff.
    rubric: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)
    starter_code: Mapped[str | None] = mapped_column(Text)
    # Nullable so every assignment that existed before subjects did keeps
    # working untouched: no subject means Python, which is what they all were.
    subject_id: Mapped[int | None] = mapped_column(ForeignKey("subjects.id"), index=True)

    subject: Mapped["Subject | None"] = relationship()

    @property
    def checker(self) -> str:
        """Which phase-3 checker this assignment is graded by."""
        return self.subject.checker if self.subject else "python"

    @property
    def rubric_items(self) -> list[dict]:
        items = (self.rubric or {}).get("items") or []
        return sorted(items, key=lambda i: i.get("priority", 99))

    @property
    def entry_point(self) -> str | None:
        return (self.rubric or {}).get("entry_point")


class Specification(Base):
    """Versioned — every clarification round writes a new row, so drift can be
    measured against version 1 as well as against the locked version."""

    __tablename__ = "specifications"
    __table_args__ = (UniqueConstraint("assignment_id", "student_id", "version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey("assignments.id"), index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")  # draft | locked
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # Gate bookkeeping: how many times each rubric item has been asked about,
    # and which gaps the student explicitly confirmed as "my answer stands".
    gap_ask_counts: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)
    confirmed_gaps: Mapped[list] = mapped_column(JSONColumn, nullable=False, default=list)
    # How often the student pressed "I don't know" per gap — a signal that the
    # question, not the student, may be the problem.
    dont_know_counts: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)
    # The question on the table when the student last replied, so a short answer
    # ("an empty list") can be recorded with the condition it answers.
    last_question: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    assignment: Mapped[Assignment] = relationship()
    student: Mapped[Student] = relationship(back_populates="specifications")
    snapshots: Mapped[list["CodeSnapshot"]] = relationship(
        back_populates="specification", cascade="all, delete-orphan"
    )
    interactions: Mapped[list["Interaction"]] = relationship(
        back_populates="specification", cascade="all, delete-orphan"
    )
    diff_results: Mapped[list["DiffResult"]] = relationship(
        back_populates="specification", cascade="all, delete-orphan"
    )

    @property
    def is_locked(self) -> bool:
        return self.status == "locked"


class CodeSnapshot(Base):
    __tablename__ = "code_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    specification_id: Mapped[int] = mapped_column(ForeignKey("specifications.id"), index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    is_final: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    specification: Mapped[Specification] = relationship(back_populates="snapshots")


class Interaction(Base):
    """One row per Tutor Layer exchange — raw material for Reflection and the
    Dependency Profile."""

    __tablename__ = "interactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    specification_id: Mapped[int] = mapped_column(ForeignKey("specifications.id"), index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    student_message: Mapped[str] = mapped_column(Text, nullable=False)
    classified_type: Mapped[str | None] = mapped_column(String(30))  # factual | problem_solving
    ai_response: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # answer | counter_question | hint | diagnostic
    # ('hint' = escape hatch, assist cost logged; 'diagnostic' = reflection phase)
    response_mode: Mapped[str] = mapped_column(String(30), nullable=False, default="answer")

    confidence: Mapped[float | None] = mapped_column(Float)
    reasoning: Mapped[str | None] = mapped_column(Text)
    assist_cost: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    thread_round: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Set when the exchange happened during reflection, about one discrepancy,
    # so the transcript and the dependency profile stay one log.
    diff_result_id: Mapped[int | None] = mapped_column(ForeignKey("diff_results.id"))

    specification: Mapped[Specification] = relationship(back_populates="interactions")


class GateTurn(Base):
    """One exchange in the Specification Gate.

    Interactions are keyed to a single specification row, but the gate writes a
    NEW version almost every turn — so gate dialogue has no stable row to hang
    off. Keying on (assignment, student) instead keeps the whole conversation
    together across versions, which is what makes a transcript possible.
    """

    __tablename__ = "gate_turns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey("assignments.id"), index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), index=True)
    specification_id: Mapped[int | None] = mapped_column(ForeignKey("specifications.id"))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    student_message: Mapped[str | None] = mapped_column(Text)
    question: Mapped[str | None] = mapped_column(Text)
    gap_id: Mapped[str | None] = mapped_column(String(200))
    # answer | dont_know | override | locked
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="answer")
    # set when the reply did not settle the item and it was asked again
    shortfall: Mapped[str | None] = mapped_column(Text)
    version_after: Mapped[int | None] = mapped_column(Integer)


class DiffResult(Base):
    __tablename__ = "diff_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    specification_id: Mapped[int] = mapped_column(ForeignKey("specifications.id"), index=True)
    code_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("code_snapshots.id"))
    category: Mapped[str] = mapped_column(String(20), nullable=False)  # drift | revision | bug
    description: Mapped[str] = mapped_column(Text, nullable=False)
    student_explanation: Mapped[str | None] = mapped_column(Text)
    instructor_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    spec_item_key: Mapped[str | None] = mapped_column(String(200))
    evidence: Mapped[str | None] = mapped_column(Text)
    test_detail: Mapped[dict | None] = mapped_column(JSONColumn)
    # One Socratic question per discrepancy, to help the student work out WHY
    # their code diverged. It never names the cause — the explanation they owe
    # is still theirs to write.
    diagnostic_question: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    specification: Mapped[Specification] = relationship(back_populates="diff_results")


class SubmissionAttempt(Base):
    """One row per submission, kept forever.

    `submit()` replaces the DiffResult rows each time, so without this table a
    student who submitted four times looks identical to one who got it right
    first try — and "how long between retries" has nothing to measure. This is
    the retry history; the DiffResults remain the current verdict.
    """

    __tablename__ = "submission_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    specification_id: Mapped[int] = mapped_column(ForeignKey("specifications.id"), index=True)
    # Denormalised so per-student and per-class rollups don't need the join.
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), index=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey("assignments.id"), index=True)
    code_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("code_snapshots.id"))

    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    cases_run: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cases_passed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    differences: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Seconds since the previous attempt on this specification. None on the
    # first one, which is what makes "mean time between retries" honest.
    seconds_since_previous: Mapped[int | None] = mapped_column(Integer)


# --------------------------------------------------------------------------
# Operational tables
# --------------------------------------------------------------------------
class LLMCall(Base):
    """Every generate_structured() call lands here. Read it before the demo:
    if Groq is absorbing most of a burst, that is the RPM cap biting."""

    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    task: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    tier: Mapped[str] = mapped_column(String(20), nullable=False, default="classification")
    provider: Mapped[str] = mapped_column(String(20), nullable=False)  # mistral | groq | fake
    model: Mapped[str | None] = mapped_column(String(100))
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    fell_back: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error_type: Mapped[str | None] = mapped_column(String(80))
    error_detail: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class CourseSettings(Base):
    """Dependency-profile visibility is a consent question, so it lives in the
    data model rather than in the dashboard UI (Section 3.5)."""

    __tablename__ = "course_settings"

    course_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    dependency_profile_visibility: Mapped[str] = mapped_column(
        String(20), nullable=False, default="student_only"
    )  # student_only | instructor_opt_in


class Course(Base):
    """The registry backing the instructor dashboard's course switcher.

    Nothing else in the data model requires a row here — Student/Assignment/
    CourseSettings all just key on a free-text course_id — so this table
    exists purely so a course created from the dashboard, with no students or
    exercises yet, still shows up in the dropdown and survives a reload
    instead of only living in that one browser tab. Course ids that already
    have students or assignments (like the seeded CS101) are recognized too,
    without needing a row here — see list_courses() in dashboard.py."""

    __tablename__ = "courses"

    course_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
