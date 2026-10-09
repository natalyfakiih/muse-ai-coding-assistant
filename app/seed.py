"""Seed a course, students and two assignments.

    python -m app.seed          # create if empty
    python -m app.seed --reset  # drop and recreate
"""
from __future__ import annotations

import sys

from app.db import Base, SessionLocal, engine, init_db
from app.models import Assignment, CourseSettings, Student

COURSE_ID = "CS101"

STUDENTS = [
    ("Rim T.", "A"), ("Nataly F.", "A"), ("Karim H.", "A"), ("Lea M.", "B"),
    ("Omar S.", "B"), ("Maya K.", "B"), ("Jad A.", "C"), ("Nour Z.", "C"),
]

ASSIGNMENTS = [
    {
        "prompt_text": (
            "Write a function that returns the first n numbers of the Fibonacci "
            "sequence as a list. Decide for yourself what should happen for n = 0 "
            "and for negative n — then state it in your specification before you "
            "write any code."
        ),
        "rubric": {
            "entry_point": "fib",
            "items": [
                {"id": "goal", "priority": 1, "field": "goal",
                 "text": "must state in one sentence what the function does"},
                {"id": "inputs", "priority": 2, "field": "inputs",
                 "text": "must name the input, its type, and any constraint on it"},
                {"id": "outputs", "priority": 3, "field": "outputs",
                 "text": "must state what is returned and its type"},
                {"id": "empty_input", "priority": 4, "field": None,
                 "text": "must state the behavior when n = 0"},
                {"id": "negative_input", "priority": 5, "field": None,
                 "text": "must state the behavior for negative n"},
                {"id": "examples", "priority": 6, "field": "examples",
                 "text": "must give at least one concrete input/output example"},
            ],
        },
        "starter_code": "def fib(n):\n    # your code here\n    pass\n",
    },
    {
        "prompt_text": (
            "Write a function that takes a list of integers and returns the list "
            "with duplicates removed, preserving the original order. Decide what "
            "happens with an empty list before you start."
        ),
        "rubric": {
            "entry_point": "dedupe",
            "items": [
                {"id": "goal", "priority": 1, "field": "goal",
                 "text": "must state in one sentence what the function does"},
                {"id": "inputs", "priority": 2, "field": "inputs",
                 "text": "must name the input list and the type of its elements"},
                {"id": "outputs", "priority": 3, "field": "outputs",
                 "text": "must state what is returned and its type"},
                {"id": "order", "priority": 4, "field": None,
                 "text": "must state whether the original order is preserved"},
                {"id": "empty_input", "priority": 5, "field": None,
                 "text": "must state the behavior for an empty list"},
                {"id": "examples", "priority": 6, "field": "examples",
                 "text": "must give at least one concrete input/output example"},
            ],
        },
        "starter_code": "def dedupe(items):\n    # your code here\n    pass\n",
    },
    {
        "prompt_text": (
            "Write a function that tells you whether a whole number is even or "
            "odd. Two things are yours to decide before you write any code: what "
            "the function hands back — a true/false answer, or the words "
            "themselves — and what it does with 0 and with negative numbers."
        ),
        "rubric": {
            # Deliberately neutral: `is_even` would push every student toward
            # returning a boolean, and the return type is the decision the
            # assignment is actually about.
            "entry_point": "even_or_odd",
            "items": [
                {"id": "goal", "priority": 1, "field": "goal",
                 "text": "must state in one sentence what the function does"},
                {"id": "inputs", "priority": 2, "field": "inputs",
                 "text": "must name the input and its type"},
                {"id": "outputs", "priority": 3, "field": "outputs",
                 "text": "must state what is returned and its type — true/false, "
                         "or the words \"even\" and \"odd\""},
                {"id": "zero", "priority": 4, "field": None,
                 "text": "must state what the function does when the number is 0"},
                {"id": "negative", "priority": 5, "field": None,
                 "text": "must state what the function does for negative numbers"},
                {"id": "examples", "priority": 6, "field": "examples",
                 "text": "must give at least one concrete input/output example"},
            ],
        },
        "starter_code": "def even_or_odd(n):\n    # your code here\n    pass\n",
    },
]


def seed(reset: bool = False) -> None:
    if reset:
        Base.metadata.drop_all(bind=engine)
    init_db()

    db = SessionLocal()
    try:
        known = {s.name for s in db.query(Student).filter(Student.course_id == COURSE_ID).all()}
        missing = [(n, c) for n, c in STUDENTS if n not in known]
        if missing:
            db.add_all([Student(name=n, course_id=COURSE_ID, cohort=c) for n, c in missing])
        # Insert only what's missing, matched on the rubric's entry_point, so
        # adding an assignment doesn't mean throwing away everyone's work.
        # Editing an EXISTING assignment still needs --reset: overwriting a
        # rubric in place would silently invalidate specifications already
        # locked against it.
        existing = {
            (a.rubric or {}).get("entry_point")
            for a in db.query(Assignment).filter(Assignment.course_id == COURSE_ID).all()
        }
        added = [a for a in ASSIGNMENTS if a["rubric"]["entry_point"] not in existing]
        if added:
            db.add_all([Assignment(course_id=COURSE_ID, **a) for a in added])
        if db.get(CourseSettings, COURSE_ID) is None:
            # Default: student-facing only. Flip to instructor_opt_in explicitly.
            db.add(CourseSettings(course_id=COURSE_ID,
                                  dependency_profile_visibility="student_only"))
        db.commit()
        print(f"seeded: {db.query(Student).count()} students, "
              f"{db.query(Assignment).count()} assignments in {COURSE_ID}"
              + (f" (added {len(added)} new assignment(s))" if added else ""))
    finally:
        db.close()


if __name__ == "__main__":
    seed(reset="--reset" in sys.argv)
