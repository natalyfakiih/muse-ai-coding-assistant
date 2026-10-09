"""Seed a course, students and two assignments.

    python -m app.seed          # create if empty
    python -m app.seed --reset  # drop and recreate
"""
from __future__ import annotations

import sys

from app.db import Base, SessionLocal, engine, init_db
from app.models import (
    Assignment,
    CourseSettings,
    Student,
    Subject,
    SubjectMaterial,
)

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


# --------------------------------------------------------------------------
# Subjects. The `checker` is the field with teeth: it decides what phase 3
# actually does. Phases 1 and 2 are the same for every subject, because stating
# your plan before you act, and getting a question back instead of an answer,
# is not a programming idea.
# --------------------------------------------------------------------------
SUBJECTS = [
    {
        "name": "Programming",
        "checker": "python",
        "description": "Python exercises, checked by running the code.",
        # Ollie answers out of these and nothing else, and the Learn page
        # teaches from them, so they are written as an instructor would teach
        # the course — including the conventions this course marks against.
        #
        # They deliberately teach the ingredients and never the recipe. Every
        # worked example is on a task that is NOT one of the exercises: a note
        # that walked through removing duplicates would hand over the answer to
        # the exercise that asks for exactly that, and the whole system exists
        # to stop a student being handed code they did not write.
        "materials": [
            {
                "title": "What a function hands back",
                "body": (
                    "A function in this course returns a value. Printing is not "
                    "returning: print puts characters on the screen and then the "
                    "function ends with nothing to give the code that called it. "
                    "If you print your result instead of returning it, the function "
                    "hands back None, and anything checking your work sees None.\n\n"
                    "Decide the type of what comes back before you write the body, "
                    "and keep it the same on every path through the function. A "
                    "function that returns a list most of the time and the string "
                    "\"error\" in one case has two return types, and whoever uses it "
                    "now has to check which one they got. If a case genuinely has no "
                    "answer, returning an empty value of the same type, or raising an "
                    "error, are both defensible; returning something of a different "
                    "type is not.\n\n"
                    "Every path must return. A branch that falls off the end of a "
                    "function returns None by accident, and that None usually "
                    "surfaces much later, somewhere that has nothing to do with the "
                    "line that caused it."
                ),
            },
            {
                "title": "Deciding the edge cases before you write code",
                "body": (
                    "An edge case is an input that is legal but sits at the boundary "
                    "of what you had in mind: the empty list, zero, a negative "
                    "number, a list with one item, a value that appears twice. They "
                    "are where nearly all wrong behaviour lives, because they are the "
                    "inputs nobody pictured while writing the loop.\n\n"
                    "In this course an empty input is a normal input, not an error, "
                    "unless you state otherwise in your specification. Whichever you "
                    "choose, you have to say which before you start, because your "
                    "code is checked against what you said it would do and not "
                    "against what seems reasonable afterwards.\n\n"
                    "There is usually no single right answer. For a negative count, "
                    "returning an empty result and raising a ValueError are both "
                    "defensible choices; the one thing that is not defensible is not "
                    "having decided. Write the decision as a sentence concrete enough "
                    "to test: \"for n = 0 it returns an empty list\" can be checked, "
                    "and \"it handles zero sensibly\" cannot."
                ),
            },
            {
                "title": "Lists: order, membership and sets",
                "body": (
                    "A list keeps its order. The order you put things in is the order "
                    "you get them back, and that holds for every operation that does "
                    "not explicitly sort or reverse.\n\n"
                    "The expression x in items asks whether a value is somewhere in "
                    "the list. On a list, Python answers it by looking at the "
                    "elements one after another until it finds a match, so the work "
                    "grows with the length of the list. Inside a loop that already "
                    "runs once per element, that means the total work grows with the "
                    "square of the length, which is fine for a hundred items and slow "
                    "for a hundred thousand.\n\n"
                    "A set answers the same question in roughly constant time, "
                    "whatever its size, because it hashes the value rather than "
                    "scanning. The trade is that a set has no order and keeps no "
                    "duplicates. Converting a list to a set and back therefore "
                    "answers \"which values are here\" quickly, but it does not "
                    "preserve the order they arrived in, and in Python the order you "
                    "get back is not one you can rely on. If order matters to your "
                    "specification, the set can hold what you have already seen while "
                    "the list stays in charge of the sequence."
                ),
            },
            {
                "title": "Building a result with a loop",
                "body": (
                    "The usual shape is three lines of thinking: start with an empty "
                    "result, walk over the input, and add to the result as you go. "
                    "The things to get right are what the result starts as, what "
                    "decides whether an item is added, and that you return the result "
                    "after the loop rather than inside it.\n\n"
                    "Returning inside the loop is the most common bug of this shape. "
                    "A return runs once and ends the function immediately, so a "
                    "return placed in the body means the function stops on the first "
                    "item and you get a result of length one.\n\n"
                    "Here the task is collecting the lengths of some words, which is "
                    "not any of your exercises:\n\n"
                    "    def lengths(words):\n"
                    "        out = []\n"
                    "        for word in words:\n"
                    "            out.append(len(word))\n"
                    "        return out\n\n"
                    "Note what happens with no input at all. If words is empty the "
                    "loop body never runs, out is still the empty list it started "
                    "as, and the function returns that. The empty case usually falls "
                    "out of this shape for free, which is worth checking rather than "
                    "assuming."
                ),
            },
            {
                "title": "Remainders, and what Python does with negatives",
                "body": (
                    "The remainder operator % gives what is left after division. "
                    "7 % 3 is 1, and 8 % 4 is 0. A number is even exactly when its "
                    "remainder on division by 2 is 0, which makes n % 2 == 0 the "
                    "usual test.\n\n"
                    "Negative numbers are where this bites, because Python does not "
                    "behave like C or Java here. In Python the result of % takes the "
                    "sign of the right-hand side, so -3 % 2 is 1, not -1. That is "
                    "convenient: n % 2 == 0 identifies even numbers correctly for "
                    "negatives with no special case, and so does n % 2 == 1 for odd "
                    "ones. If you carry the habit of writing n % 2 == 1 from another "
                    "language, test it on a negative number there before you trust "
                    "it, because in several of them it is false for every negative "
                    "odd number.\n\n"
                    "Zero is even. It divides by 2 with no remainder, and it is worth "
                    "saying out loud in your specification because a student who "
                    "thinks of even numbers as counting 2, 4, 6 often leaves zero "
                    "undecided."
                ),
            },
        ],
    },
    {
        "name": "Mechanics",
        "checker": "math",
        "description": "Motion under constant acceleration, checked symbolically.",
        # Ollie answers out of these notes and nothing else, so they are written
        # the way an instructor would actually teach the topic — including the
        # conventions this course marks against.
        "materials": [
            {
                "title": "Constant acceleration: the three equations",
                "body": (
                    "When acceleration is constant, three equations relate the five "
                    "quantities: initial velocity u, final velocity v, acceleration a, "
                    "displacement s, and time t.\n\n"
                    "    v = u + a t\n"
                    "    s = u t + (1/2) a t^2\n"
                    "    v^2 = u^2 + 2 a s\n\n"
                    "Each equation leaves out exactly one quantity. The third leaves out "
                    "time, which is why it is the one to reach for when a question gives "
                    "you a distance and asks for a speed, or the other way round. "
                    "Choosing the equation that omits the quantity you were never given "
                    "saves you solving for it first.\n\n"
                    "These hold only while a is constant. If the acceleration changes "
                    "part way through, split the motion into stages and apply them to "
                    "each stage separately."
                ),
            },
            {
                "title": "Free fall near the Earth's surface",
                "body": (
                    "An object falling freely near the surface of the Earth accelerates "
                    "downward at g = 9.81 m/s^2. In this course we ignore air resistance "
                    "unless a question says otherwise.\n\n"
                    "Dropped from rest means u = 0, which simplifies the equations: "
                    "v = g t, and v^2 = 2 g s.\n\n"
                    "Watch the sign convention. Choose a positive direction before you "
                    "start and keep it for the whole problem. If you take downward as "
                    "positive then g is +9.81 and a falling object's displacement is "
                    "positive; if you take upward as positive then g is -9.81. Most "
                    "wrong answers in this topic are a sign, not a method."
                ),
            },
            {
                "title": "Units, exactness and how answers are marked",
                "body": (
                    "Every numerical answer carries its unit. An answer of 19.8 is not "
                    "the same answer as 19.8 m/s, and it is marked as incomplete.\n\n"
                    "Give three significant figures unless the question asks for "
                    "something else. Keep full precision through the working and round "
                    "only at the end: rounding at each step accumulates error that can "
                    "move the final digit.\n\n"
                    "Where a value is exact, such as a fraction or a surd, leave it "
                    "exact rather than converting to a decimal. 1/3 is exact; 0.333 is "
                    "an approximation of it."
                ),
            },
        ],
    },
]

MATH_ASSIGNMENTS = [
    {
        "prompt_text": (
            "A ball is dropped from rest and falls 20 m to the ground. How fast is "
            "it moving at the instant it lands? Before you calculate anything, say "
            "what you are given, what you are solving for, and what form your "
            "answer will take."
        ),
        "rubric": {
            "expected_answer": "19.8 m/s",
            "tolerance": 0.05,
            "items": [
                {"id": "goal", "priority": 1, "field": "goal",
                 "text": "must state in one sentence what you are solving for"},
                {"id": "given", "priority": 2, "field": "inputs",
                 "text": "must list the quantities you were given, with their units"},
                {"id": "form", "priority": 3, "field": "outputs",
                 "text": "must state the form of your answer: its unit, and whether "
                         "it is exact or a decimal"},
                {"id": "equation", "priority": 4, "field": None,
                 "text": "must say which equation of motion you will use and why that one"},
                {"id": "sign", "priority": 5, "field": None,
                 "text": "must state which direction you are taking as positive"},
            ],
        },
        "starter_code": None,
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
        # Subjects first: an assignment points at one, and the subject is what
        # decides which checker phase 3 runs.
        by_name = {s.name: s for s in db.query(Subject)
                   .filter(Subject.course_id == COURSE_ID).all()}
        for spec in SUBJECTS:
            if spec["name"] in by_name:
                continue
            subject = Subject(course_id=COURSE_ID, name=spec["name"],
                              checker=spec["checker"], description=spec["description"])
            subject.materials = [
                SubjectMaterial(title=m["title"], body=m["body"], position=i)
                for i, m in enumerate(spec["materials"])
            ]
            db.add(subject)
            by_name[spec["name"]] = subject
        db.flush()

        # Notes added to a subject that already exists. The loop above skips a
        # subject it has seen before, which is right for the subject's own
        # fields — rewriting a checker under locked specifications would be
        # destructive — but it also meant seeded notes could only ever arrive
        # with a brand new subject. A course created before a note was written
        # would never receive it without --reset, which costs everyone's work.
        #
        # Additive and matched on title: a note the subject already has is left
        # exactly as it is, so an instructor's own edits are never overwritten,
        # and nothing is ever removed.
        for spec in SUBJECTS:
            subject = by_name[spec["name"]]
            have = {(m.title or "").strip() for m in subject.materials}
            position = len(subject.materials)
            for note in spec["materials"]:
                if note["title"].strip() in have:
                    continue
                db.add(SubjectMaterial(subject_id=subject.id, title=note["title"],
                                       body=note["body"], position=position))
                position += 1
        db.flush()

        # A maths assignment has no entry point, so identity cannot be that
        # alone. Each seeded assignment is identified by whichever of the two
        # its rubric actually has.
        def identity(rubric: dict) -> str:
            return str((rubric or {}).get("entry_point")
                       or (rubric or {}).get("expected_answer") or "")

        existing = {identity(a.rubric) for a in db.query(Assignment)
                    .filter(Assignment.course_id == COURSE_ID).all()}

        added = [a for a in ASSIGNMENTS if identity(a["rubric"]) not in existing]
        if added:
            db.add_all([Assignment(course_id=COURSE_ID,
                                   subject_id=by_name["Programming"].id, **a)
                        for a in added])

        # Assignments that existed before subjects did have subject_id NULL, so
        # the subject picker would filter them out and a course's whole back
        # catalogue would quietly become unreachable. Attaching them to
        # Programming changes nothing about how they are marked — `checker`
        # already defaulted to python for exactly these — it only makes them
        # visible again. Rubrics, specifications and student work are untouched.
        orphans = db.query(Assignment).filter(
            Assignment.course_id == COURSE_ID,
            Assignment.subject_id.is_(None),
        ).all()
        for row in orphans:
            row.subject_id = by_name["Programming"].id

        added_math = [a for a in MATH_ASSIGNMENTS if identity(a["rubric"]) not in existing]
        if added_math:
            db.add_all([Assignment(course_id=COURSE_ID,
                                   subject_id=by_name["Mechanics"].id, **a)
                        for a in added_math])
        added = added + added_math
        if db.get(CourseSettings, COURSE_ID) is None:
            # Default: student-facing only. Flip to instructor_opt_in explicitly.
            db.add(CourseSettings(course_id=COURSE_ID,
                                  dependency_profile_visibility="student_only"))
        db.commit()
        if orphans:
            print(f"attached {len(orphans)} existing assignment(s) to Programming "
                  f"(nothing else changed)")
        print(f"seeded: {db.query(Student).count()} students, "
              f"{db.query(Assignment).count()} assignments in {COURSE_ID}"
              + (f" (added {len(added)} new assignment(s))" if added else ""))
    finally:
        db.close()


if __name__ == "__main__":
    seed(reset="--reset" in sys.argv)
