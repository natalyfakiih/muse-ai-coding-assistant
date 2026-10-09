"""Request/response bodies. Kept thin — modules return plain dicts."""
from __future__ import annotations

from pydantic import BaseModel, Field


class SpecMessage(BaseModel):
    student_id: int
    message: str = Field(min_length=1, max_length=8000)


class SpecLock(BaseModel):
    student_id: int
    gap_id: str | None = None


class TutorMessage(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    code: str | None = Field(default=None, max_length=200_000)
    code_diff: str | None = Field(default=None, max_length=200_000)


class EscapeHatchRequest(BaseModel):
    message: str | None = Field(default=None, max_length=8000)


class SnapshotBody(BaseModel):
    code: str = Field(max_length=200_000)
    is_final: bool = False


class SubmitBody(BaseModel):
    code: str | None = Field(default=None, max_length=200_000)


class ExplanationBody(BaseModel):
    explanation: str = Field(min_length=1, max_length=8000)


class VisibilityBody(BaseModel):
    visibility: str = Field(pattern="^(student_only|instructor_opt_in)$")


class FlagBody(BaseModel):
    instructor_flag: bool


class RubricItemIn(BaseModel):
    """One instructor-authored checklist line. `id` is optional — the API
    slugifies the text when it's left out. `field` is deliberately not
    exposed here: spec_gate.required_field() infers a structural field
    (goal/inputs/outputs/examples) from the wording itself, which is what
    the seed assignments already rely on for their "examples" item."""
    text: str = Field(min_length=1, max_length=300)
    id: str | None = Field(default=None, max_length=60)


class AssignmentCreate(BaseModel):
    course_id: str = Field(min_length=1, max_length=100)
    prompt_text: str = Field(min_length=1, max_length=4000)
    # The function name the student's code must define — the Reflection
    # Engine's behavioral diff calls this by name.
    # Required for Python exercises, unused for maths ones (checked in the API,
    # because which one applies depends on the subject).
    entry_point: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=80)
    starter_code: str | None = Field(default=None, max_length=20_000)
    items: list[RubricItemIn] = Field(min_length=1, max_length=20)
    # Which subject the exercise sits under on the student's My learning page.
    subject_id: int | None = None
    # Maths subjects: the answer phase 3 compares against, and how close counts.
    expected_answer: str | None = Field(default=None, max_length=200)
    tolerance: float | None = Field(default=None, ge=0, le=1)


class CourseCreate(BaseModel):
    # Used as a URL path segment (/courses/{course_id}/...) elsewhere in the
    # API, so kept to characters that never need escaping there.
    course_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")

class SubjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    checker: str = Field(default="python", max_length=30)
    description: str | None = Field(default=None, max_length=2000)


class MaterialCreate(BaseModel):
    title: str = Field(default="", max_length=200)
    body: str = Field(min_length=1, max_length=200_000)
    position: int | None = None


class QuestionBody(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
