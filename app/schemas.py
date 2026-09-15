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
