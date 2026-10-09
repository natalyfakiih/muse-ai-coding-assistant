"""Populate a course with plausible finished work, for demos and screenshots.

    python -m app.demo            # add demo work (refuses if some already exists)
    python -m app.demo --force    # replace any demo work already there
    python -m app.demo --clear    # remove it and leave the seeded course empty

An empty Instructor Dashboard shows nothing about whether the dashboard is any
good. This writes the rows a real cohort would have left behind — specification
versions, gate turns, tutor exchanges, code snapshots and behavioral diffs — so
every panel has something honest to aggregate.

Two rules this file follows:

* No LLM calls. Every string here is written down, not generated, so running
  this costs nothing, works offline, and produces the same database every time
  (`random` is seeded). A demo that drifts between runs is a demo you cannot
  screenshot twice.
* The failures cluster. `class_patterns` groups by (spec item, category), and
  its whole purpose is to surface the thing half the class got wrong — so the
  data has a real headline in it (the empty-list edge case) rather than eight
  students failing eight unrelated things.

Rim T. is deliberately left out. She is the account used to demo the student
side live, and pre-filling her history would overwrite what the demo produces.
"""
from __future__ import annotations

import random
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import SessionLocal, init_db
from app.models import (
    Assignment,
    CodeSnapshot,
    CourseSettings,
    DiffResult,
    GateTurn,
    Interaction,
    LLMCall,
    Specification,
    Student,
    SubmissionAttempt,
)
from app.seed import COURSE_ID
from app.seed import seed as seed_base

RNG = random.Random(20260916)

# The demo runs "two weeks of coursework" ending yesterday, so the dashboard is
# never showing work dated in the future.
END = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
START = END - timedelta(days=13)

LIVE_DEMO_STUDENT = "Rim T."  # kept empty on purpose — see the module docstring


# --------------------------------------------------------------------------
# Cohort behaviour. The dashboard exists to make these differences visible, so
# they have to actually differ in the data and not just in the labels.
# --------------------------------------------------------------------------
COHORTS = {
    "A": {"versions": (2, 4), "questions": (1, 3), "factual_share": 0.25,
          "hints": (0, 0), "diffs": (0, 1), "explains": 1.0, "finishes": 1.0},
    "B": {"versions": (4, 6), "questions": (3, 6), "factual_share": 0.45,
          "hints": (0, 1), "diffs": (1, 2), "explains": 0.7, "finishes": 0.85},
    "C": {"versions": (6, 9), "questions": (6, 11), "factual_share": 0.65,
          "hints": (2, 3), "diffs": (2, 3), "explains": 0.35, "finishes": 0.6},
}


# --------------------------------------------------------------------------
# Per-assignment material, keyed on the rubric's entry point.
# --------------------------------------------------------------------------
ASSIGNMENT_DATA = {
    "fib": {
        "spec": {
            "goal": "return the first n numbers of the fibonacci sequence as a list",
            "inputs": [{"name": "n", "type": "int", "constraints": "whole number"}],
            "outputs": [{"name": "sequence", "type": "list of ints"}],
            "edge_cases": ["when n is 0 the function returns an empty list",
                           "for negative n the function returns an empty list"],
            "examples": [{"input": "n=5", "output": "[0, 1, 1, 2, 3]"}],
            "explicit_exclusions": [],
        },
        "gate": [
            ("a function that gives back fibonacci numbers",
             "What does it take as an input, and what type is that input?", "inputs"),
            ("it takes a whole number n",
             "What comes back out, and what type is it?", "outputs"),
            ("it returns a list of the first n fibonacci numbers",
             "What should happen when n is 0?", "empty_input"),
            ("for n = 0 it gives back an empty list",
             "And for a negative n?", "negative_input"),
            ("negative n also returns an empty list",
             "Give me one concrete example: an input, and exactly what comes back.",
             "examples"),
            ("n=5 -> [0, 1, 1, 2, 3]", None, None),
        ],
        "correct": (
            "def fib(n):\n"
            "    out = []\n"
            "    a, b = 0, 1\n"
            "    for _ in range(max(n, 0)):\n"
            "        out.append(a)\n"
            "        a, b = b, a + b\n"
            "    return out\n"
        ),
        "buggy": (
            "def fib(n):\n"
            "    out = []\n"
            "    a, b = 0, 1\n"
            "    for _ in range(n + 1):\n"
            "        out.append(a)\n"
            "        a, b = b, a + b\n"
            "    return out\n"
        ),
        "questions": [
            ("what does range() do exactly", "factual"),
            ("how do i swap two variables in python", "factual"),
            ("my list has one number too many, why", "problem_solving"),
            ("is a, b = b, a + b the same as doing it on two lines", "factual"),
            ("it crashes when i pass a negative number", "problem_solving"),
            ("my loop runs forever, what did i do wrong", "problem_solving"),
            ("do i need to handle n = 1 separately", "problem_solving"),
            ("what is the difference between append and extend", "factual"),
        ],
        "diffs": [
            ("edge_case:for negative n the function returns an empty list", "drift",
             "range(n + 1) with a negative n produces an empty range, so nothing "
             "is appended — the specification's empty list happens by accident, "
             "not by decision",
             "What does range(-3) give you, and is that the same reason your "
             "specification gives for returning an empty list?"),
            ("examples[0]", "bug",
             "returns six numbers for n=5 — one more than the example declares",
             "Count the numbers your example expects, then count the times your "
             "loop body runs. Where does the extra one come from?"),
            ("edge_case:when n is 0 the function returns an empty list", "drift",
             "returns [0] for n=0 instead of the declared empty list",
             "Walk through your loop with n = 0. How many times does the body run?"),
        ],
        "revision": {
            "key": "inputs",
            "said": "i decided to raise a TypeError if n isn't an int instead of "
                    "just letting it crash, my spec doesn't mention that",
            "reply": "That is a change to the contract, not a bug. Say what it "
                     "does now and why you changed your mind.",
            "description": "raises TypeError on a non-integer n, which the "
                           "specification's input constraint never described",
            "question": "You added a rule your specification doesn't have. Should "
                        "the specification change, or the code?",
        },
    },
    "dedupe": {
        "spec": {
            "goal": "remove duplicate values from a list, keeping the first "
                    "occurrence of each and the original order",
            "inputs": [{"name": "items", "type": "list of ints", "constraints": ""}],
            "outputs": [{"name": "unique", "type": "list of ints"}],
            "edge_cases": ["when the input list is empty the function returns an empty list"],
            "examples": [{"input": "items=[1, 1, 2]", "output": "[1, 2]"}],
            "explicit_exclusions": [],
        },
        "gate": [
            ("i need to take out the duplicates from a list",
             "What should the input be called, and what type are the values in it?",
             "inputs"),
            ("the input is a list of integers called items",
             "What does the function give back, and what type is it?", "outputs"),
            ("it gives back a list of integers with no repeats",
             "Does the original order survive, or can the result come back in any order?",
             "order"),
            ("the order stays the same as the first time each number appeared",
             "What should happen if the list is empty?", "empty_input"),
            ("an empty list gives back an empty list",
             "Give me one concrete example.", "examples"),
            ("items=[1, 1, 2] -> [1, 2]", None, None),
        ],
        "correct": (
            "def dedupe(items):\n"
            "    result = []\n"
            "    for item in items:\n"
            "        if item not in result:\n"
            "            result.append(item)\n"
            "    return result\n"
        ),
        "buggy": (
            "def dedupe(items):\n"
            "    return list(set(items))\n"
        ),
        "questions": [
            ("can i just use set() for this", "factual"),
            ("does set keep the order of a list", "factual"),
            ("my output is in the wrong order", "problem_solving"),
            ("what does 'not in' actually check", "factual"),
            ("is there a one liner for this", "factual"),
            ("how do i keep the first one and drop the rest", "problem_solving"),
            ("my function returns nothing", "problem_solving"),
            ("is dict.fromkeys cheating", "factual"),
        ],
        "diffs": [
            ("edge_case:when the input list is empty the function returns an empty list",
             "drift",
             "list(set([])) returns an empty list, but nothing in the code is "
             "about the empty case — the specification's promise is unexamined",
             "Your specification makes a promise about the empty list. Which line "
             "of your code is keeping it?"),
            ("examples[0]", "bug",
             "set() discards order, so the declared example's order is not "
             "guaranteed to come back",
             "Run your function on [3, 1, 3] a few times. Is the answer the same "
             "every time, and does your specification allow what you see?"),
            ("outputs", "bug",
             "returns a list built from a set, so the declared order is not "
             "preserved even though the type matches",
             "Your specification says a list comes back, in the original order. "
             "Which half of that is set() keeping?"),
        ],
        "revision": {
            "key": "inputs",
            "said": "i want it to work on a tuple too, not just a list, so i "
                    "stopped checking the type",
            "reply": "That widens what your function accepts. Is that what your "
                     "specification says it takes?",
            "description": "accepts any iterable, where the specification "
                           "declares a list of ints",
            "question": "You widened the input on purpose. Which line of your "
                        "specification is now out of date?",
        },
    },
    "even_or_odd": {
        "spec": {
            "goal": "say whether a whole number is even or odd",
            "inputs": [{"name": "n", "type": "int", "constraints": ""}],
            "outputs": [{"name": "answer", "type": "str"}],
            "edge_cases": ["0 counts as even",
                           "negative numbers follow the same rule as positive ones"],
            "examples": [{"input": "n=4", "output": "'even'"}],
            "explicit_exclusions": [],
        },
        "gate": [
            ("tell me if a number is even or odd",
             "What does the function hand back — a true/false answer, or the "
             "words themselves?", "outputs"),
            ("it returns the word, so 'even' or 'odd'",
             "What does it take, and what type is it?", "inputs"),
            ("a whole number n", "What does it do when the number is 0?", "zero"),
            ("0 is even", "And for negative numbers?", "negative"),
            ("negatives work the same way, -3 is odd",
             "Give me one concrete example.", "examples"),
            ("n=4 -> 'even'", None, None),
        ],
        "correct": (
            "def even_or_odd(n):\n"
            "    if n % 2 == 0:\n"
            "        return \"even\"\n"
            "    return \"odd\"\n"
        ),
        "buggy": (
            "def even_or_odd(n):\n"
            "    return n % 2 == 0\n"
        ),
        "questions": [
            ("what does the % operator do", "factual"),
            ("is -3 % 2 equal to 1 or -1 in python", "factual"),
            ("should i return True or the word", "problem_solving"),
            ("my function returns True instead of 'even'", "problem_solving"),
            ("why is 0 even", "factual"),
            ("do i need an else", "factual"),
            ("negative numbers give me the wrong answer", "problem_solving"),
        ],
        "diffs": [
            ("outputs", "drift",
             "returns a boolean where the specification declares the words "
             "'even' and 'odd'",
             "Your specification names two strings. What does n % 2 == 0 "
             "evaluate to?"),
            ("edge_case:0 counts as even", "drift",
             "0 is never handled separately, so the declared decision about it "
             "is not visible anywhere in the code",
             "Your specification singles out 0. Does your code need to, or does "
             "the general rule already cover it? Either answer is fine — say which."),
            ("examples[0]", "bug",
             "returns True where the example declares the string 'even'",
             "Compare the type of what you return with the type in your example."),
        ],
        "revision": {
            "key": "outputs",
            "said": "i changed my mind halfway and made it return True/False "
                    "because that was easier to use, i know my spec says the words",
            "reply": "Changing your mind is allowed. What made the boolean "
                     "easier, and what does your specification say now?",
            "description": "returns a boolean after the student deliberately "
                           "changed the contract mid-implementation",
            "question": "You chose the boolean on purpose. What would the "
                        "specification have to say for that to be the right call?",
        },
    },
}


# --------------------------------------------------------------------------
def _at(day: int, hour: int, minute: int = 0) -> datetime:
    return START + timedelta(days=day, hours=hour, minutes=minute)


def _working_moment(day_offset: int) -> datetime:
    """A plausible time to be doing coursework: evenings and weekend afternoons,
    never 04:00, because a dashboard full of 4am timestamps looks fake."""
    hour = RNG.choice([10, 11, 14, 15, 16, 19, 20, 21, 21, 22])
    return _at(day_offset, hour, RNG.randint(0, 59))


def _partial(spec: dict, step: int, total: int) -> dict:
    """Version `step` of a specification being filled in one field at a time —
    which is what the version history is meant to show."""
    order = ["goal", "inputs", "outputs", "edge_cases", "examples"]
    out = {"goal": "", "inputs": [], "outputs": [], "edge_cases": [],
           "examples": [], "explicit_exclusions": []}
    revealed = max(1, round(len(order) * step / total))
    for field in order[:revealed]:
        out[field] = spec[field]
    return out


def _clear(db) -> int:
    """Remove every row the demo writes, leaving students and assignments."""
    students = db.scalars(
        select(Student).where(Student.course_id == COURSE_ID,
                              Student.name != LIVE_DEMO_STUDENT)
    ).all()
    ids = [s.id for s in students]
    if not ids:
        return 0
    specs = db.scalars(
        select(Specification).where(Specification.student_id.in_(ids))
    ).all()
    spec_ids = [s.id for s in specs]
    removed = 0
    if spec_ids:
        # Children first: Interaction points at DiffResult, so that order matters.
        for model, column in ((Interaction, Interaction.specification_id),
                              (SubmissionAttempt, SubmissionAttempt.specification_id),
                              (DiffResult, DiffResult.specification_id),
                              (CodeSnapshot, CodeSnapshot.specification_id)):
            for row in db.scalars(select(model).where(column.in_(spec_ids))).all():
                db.delete(row)
                removed += 1
        db.flush()
    for row in db.scalars(select(GateTurn).where(GateTurn.student_id.in_(ids))).all():
        db.delete(row)
        removed += 1
    for spec in specs:
        db.delete(spec)
        removed += 1
    db.commit()
    return removed


# --------------------------------------------------------------------------
def _one_attempt(db, student: Student, assignment: Assignment, data: dict,
                 profile: dict, day: int) -> None:
    """Everything one student left behind on one assignment."""
    target = data["spec"]
    version_count = RNG.randint(*profile["versions"])
    opened = _working_moment(day)

    specs: list[Specification] = []
    for index in range(version_count):
        spec = Specification(
            assignment_id=assignment.id,
            student_id=student.id,
            version=index + 1,
            content=_partial(target, index + 1, version_count),
            status="draft",
            created_at=opened + timedelta(minutes=4 * index + RNG.randint(0, 3)),
            gap_ask_counts={}, confirmed_gaps=[], dont_know_counts={},
        )
        db.add(spec)
        specs.append(spec)
    db.flush()

    latest = specs[-1]
    latest.content = dict(target)
    latest.status = "locked"

    # --- phase 1 transcript -------------------------------------------------
    for turn_index, (said, asked, gap) in enumerate(data["gate"]):
        db.add(GateTurn(
            assignment_id=assignment.id,
            student_id=student.id,
            specification_id=specs[min(turn_index, len(specs) - 1)].id,
            timestamp=opened + timedelta(minutes=3 * turn_index + 1),
            student_message=said,
            question=asked,
            gap_id=gap,
            kind="locked" if asked is None else "answer",
            version_after=min(turn_index + 1, version_count),
        ))
    # A struggling student asks for help understanding the question itself.
    if profile["hints"][1] > 0 and RNG.random() < 0.5:
        db.add(GateTurn(
            assignment_id=assignment.id, student_id=student.id,
            specification_id=specs[1].id if len(specs) > 1 else latest.id,
            timestamp=opened + timedelta(minutes=7),
            student_message="I don't know",
            question=data["gate"][1][1],
            gap_id=data["gate"][1][2], kind="dont_know",
            version_after=min(2, version_count),
        ))

    if RNG.random() > profile["finishes"]:
        # Abandoned after locking the specification. Someone who gets that far
        # has usually opened the editor and asked something before giving up, so
        # a row of 15 specification versions against zero questions is not what
        # quitting looks like — and on the dashboard it reads as missing data.
        for index, (message, kind) in enumerate(data["questions"][:RNG.randint(1, 3)]):
            db.add(Interaction(
                specification_id=latest.id,
                student_id=student.id,
                timestamp=opened + timedelta(minutes=6 * version_count + 9 * index),
                student_message=message,
                classified_type=kind,
                ai_response=("Short answer, then try it in your own code."
                             if kind == "factual" else
                             "What did you expect that line to do?"),
                response_mode="answer" if kind == "factual" else "counter_question",
                confidence=round(RNG.uniform(0.7, 0.95), 2),
                reasoning="asked before abandoning the attempt",
                assist_cost=0,
                thread_round=index,
            ))
        db.commit()
        return  # locked the specification, never submitted code

    # --- phase 2: code and tutor --------------------------------------------
    diff_count = RNG.randint(*profile["diffs"])
    solves = RNG.random() < profile["explains"]  # did they get it clean in the end
    code = data["buggy"] if diff_count else data["correct"]
    coding_started = opened + timedelta(minutes=6 * version_count + 5)

    snapshots = []
    for index in range(RNG.randint(2, 4)):
        snapshot = CodeSnapshot(
            specification_id=latest.id,
            content=code if index else code.split("\n")[0] + "\n    pass\n",
            timestamp=coding_started + timedelta(minutes=9 * index),
            is_final=False,
        )
        db.add(snapshot)
        snapshots.append(snapshot)
    final = CodeSnapshot(specification_id=latest.id, content=code,
                         timestamp=coding_started + timedelta(minutes=48), is_final=True)
    db.add(final)
    db.flush()

    hints_left = RNG.randint(*profile["hints"])
    asked = RNG.sample(data["questions"], min(RNG.randint(*profile["questions"]),
                                              len(data["questions"])))
    for index, (message, kind) in enumerate(asked):
        if kind == "factual" and RNG.random() > profile["factual_share"]:
            kind = "problem_solving"
        if kind == "problem_solving" and hints_left > 0 and index == len(asked) - 1:
            mode, cost, reply = "hint", 1, (
                "Look at the line that decides how many times the loop runs."
                if "loop" in message or "many" in message else
                "Compare what your specification promises with what this line "
                "actually returns."
            )
            hints_left -= 1
        elif kind == "problem_solving":
            mode, cost, reply = "counter_question", 0, (
                "What did you expect that line to give you, and what did it "
                "give you instead?"
            )
        else:
            mode, cost, reply = "answer", 0, (
                "Short answer, then try it in your own code and see."
            )
        db.add(Interaction(
            specification_id=latest.id,
            student_id=student.id,
            timestamp=coding_started + timedelta(minutes=7 * index + RNG.randint(1, 5)),
            student_message=message,
            classified_type=kind,
            ai_response=reply,
            response_mode=mode,
            confidence=round(RNG.uniform(0.72, 0.97), 2),
            reasoning=("asks about the student's own code" if kind == "problem_solving"
                       else "asks about Python itself, not this solution"),
            assist_cost=cost,
            thread_round=index % 3,
        ))

    # --- a deliberate change of mind ----------------------------------------
    # "revision" is the one category that requires evidence: a quote from the
    # interaction log showing the student decided this on purpose. Writing the
    # DiffResult without also writing the conversation it cites would produce a
    # dashboard row that contradicts the rule the classifier is held to.
    revision = data.get("revision") if RNG.random() < 0.4 else None
    if revision:
        db.add(Interaction(
            specification_id=latest.id,
            student_id=student.id,
            timestamp=coding_started + timedelta(minutes=41),
            student_message=revision["said"],
            classified_type="problem_solving",
            ai_response=revision["reply"],
            response_mode="counter_question",
            confidence=0.91,
            reasoning="states a deliberate change to the locked specification",
            assist_cost=0,
            thread_round=0,
        ))

    # --- phase 3: behavioral diff -------------------------------------------
    latest.submitted_at = coding_started + timedelta(minutes=52)

    # Retry history. Someone whose code failed four cases goes back to the
    # editor and returns; someone who passed first time does not. The gap
    # between attempts is the interesting number, so it has to vary.
    tries = 1 if not diff_count else RNG.randint(2, 2 + diff_count)
    at = latest.submitted_at
    previous_at = None
    for number in range(1, tries + 1):
        last = number == tries
        gap = None if previous_at is None else max(0, int((at - previous_at).total_seconds()))
        db.add(SubmissionAttempt(
            specification_id=latest.id,
            student_id=student.id,
            assignment_id=assignment.id,
            code_snapshot_id=final.id,
            attempt_number=number,
            timestamp=at,
            cases_run=2 + diff_count,
            # Each trip back clears some of them; the last one clears the rest
            # unless they never got there.
            cases_passed=(2 + diff_count) if last and solves else 2 + number - 1,
            differences=0 if (last and solves) else max(0, diff_count - number + 1),
            seconds_since_previous=gap,
        ))
        previous_at = at
        at = at + timedelta(minutes=RNG.randint(6, 28))
    latest.submitted_at = previous_at
    if revision:
        db.add(DiffResult(
            specification_id=latest.id,
            code_snapshot_id=final.id,
            category="revision",
            description=revision["description"],
            spec_item_key=revision["key"],
            diagnostic_question=revision["question"],
            evidence=revision["said"],
            student_explanation=(
                "I changed this on purpose while I was coding and never went "
                "back to update the specification."
                if RNG.random() < profile["explains"] else None
            ),
            created_at=latest.submitted_at + timedelta(minutes=1),
        ))
    for key, category, description, question in data["diffs"][:diff_count]:
        explained = RNG.random() < profile["explains"]
        db.add(DiffResult(
            specification_id=latest.id,
            code_snapshot_id=final.id,
            category=category,
            description=description,
            spec_item_key=key,
            diagnostic_question=question,
            student_explanation=(
                "I hadn't actually thought about this case — I wrote the code "
                "for the normal input and the edge case only worked by luck."
                if explained else None
            ),
            instructor_flag=bool(not explained and RNG.random() < 0.3),
            created_at=latest.submitted_at + timedelta(minutes=1),
        ))
    db.commit()


def _llm_calls(db, count: int = 260) -> None:
    """A plausible call log, including the rate-limit failover that really
    happens on the free Mistral tier."""
    tasks = [("extract_fields", "classification"), ("next_gap", "classification"),
             ("clarifying_question", "dialogue"), ("classify_question", "classification"),
             ("socratic_question", "dialogue"), ("factual_answer", "dialogue"),
             ("classify_diff", "classification"), ("diagnostic_question", "dialogue"),
             ("materialize_edge_cases", "classification"), ("explain_gap", "dialogue")]
    for index in range(count):
        task, tier = RNG.choice(tasks)
        rate_limited = RNG.random() < 0.55
        db.add(LLMCall(
            timestamp=_working_moment(RNG.randint(0, 13)),
            task=task, tier=tier,
            provider="groq" if rate_limited else "mistral",
            model="openai/gpt-oss-20b" if rate_limited else "mistral-small-latest",
            latency_ms=RNG.randint(380, 1400) if rate_limited else RNG.randint(420, 1900),
            success=RNG.random() > 0.02,
            fell_back=rate_limited,
            error_type="RateLimitError" if rate_limited else None,
            error_detail="mistral returned 429" if rate_limited else None,
            attempts=1,
        ))
    db.commit()


# --------------------------------------------------------------------------
def build(force: bool = False, clear_only: bool = False) -> None:
    init_db()
    seed_base()  # students, assignments and course settings, if they aren't there

    db = SessionLocal()
    try:
        if clear_only:
            print(f"cleared {_clear(db)} demo rows")
            return

        students = db.scalars(
            select(Student).where(Student.course_id == COURSE_ID).order_by(Student.id)
        ).all()
        assignments = db.scalars(
            select(Assignment).where(Assignment.course_id == COURSE_ID).order_by(Assignment.id)
        ).all()
        if not students or not assignments:
            raise SystemExit("Nothing seeded yet — run `python -m app.seed` first.")

        existing = db.scalar(select(Specification).limit(1))
        if existing is not None and not force:
            raise SystemExit(
                "This database already has specification work in it. Re-run with "
                "--force to replace the demo data, or --clear to remove it."
            )
        if force:
            print(f"cleared {_clear(db)} existing demo rows")

        # Instructors can only see dependency profiles where the course opted in,
        # and a demo of a panel nobody can open is not a demo.
        settings = db.get(CourseSettings, COURSE_ID)
        if settings is not None:
            settings.dependency_profile_visibility = "instructor_opt_in"

        attempts = 0
        for student in students:
            if student.name == LIVE_DEMO_STUDENT:
                continue
            profile = COHORTS.get(student.cohort or "B", COHORTS["B"])
            # Not everyone gets to every assignment; the later ones thin out.
            reach = {"A": 3, "B": 3, "C": 2}.get(student.cohort or "B", 2)
            for index, assignment in enumerate(assignments[:reach]):
                entry = (assignment.rubric or {}).get("entry_point")
                data = ASSIGNMENT_DATA.get(entry)
                if data is None:
                    continue
                _one_attempt(db, student, assignment, data, profile,
                             day=index * 4 + RNG.randint(0, 2))
                attempts += 1

        _llm_calls(db)

        print(f"demo data written: {attempts} student-assignment attempts across "
              f"{len(students) - 1} students")
        print("Open /instructor — every panel should now have something in it.")
    finally:
        db.close()


if __name__ == "__main__":
    build(force="--force" in sys.argv, clear_only="--clear" in sys.argv)
