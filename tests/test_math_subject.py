"""Mathematics as a second subject.

Two things are being protected here.

The first is security. sympy's `parse_expr` ends in `eval()`, and Python puts
`__builtins__` into any globals dict handed to eval unless it is already there.
Before this was locked down, an "answer" of
`__import__("pathlib").Path("/tmp/x").write_text("pwned")` really did write the
file, and `open("/etc/hostname").read()` really did read it. The subprocess
limits the damage; it does not prevent it. These tests exist so that cannot
quietly come back.

The second is the thing this whole system is about: a correct answer must never
be marked wrong. The first draft of the checker read the word "fraction" in a
student's declared output form as a UNIT, and told someone who answered `1/2`
that their answer was missing its units. That is the dedupe bug again, in a new
subject.
"""
from __future__ import annotations

import pathlib
import tempfile

import pytest

from app.modules import math_check, sandbox


# --------------------------------------------------------------------------
# security
# --------------------------------------------------------------------------
ATTACKS = [
    '__import__("os").getcwd()',
    'open("/etc/hostname").read()',
    'exec("x=1")',
    'eval("1+1")',
    '(lambda: 1)()',
    '().__class__.__bases__[0].__subclasses__()',
]


@pytest.mark.parametrize("attack", ATTACKS)
def test_a_crafted_answer_cannot_run_code(attack):
    result = sandbox.run_expression(attack, "1", None)
    assert result.ok is False
    assert result.equivalent is not True


def test_a_crafted_answer_has_no_side_effect():
    """The proof, rather than the promise: if it executed, the file exists."""
    probe = pathlib.Path(tempfile.gettempdir()) / "muse_test_injection_probe.txt"
    if probe.exists():
        probe.unlink()
    try:
        sandbox.run_expression(
            f'__import__("pathlib").Path(r"{probe}").write_text("pwned")', "1", None)
        assert not probe.exists(), "the expression parser executed arbitrary code"
    finally:
        if probe.exists():
            probe.unlink()


def test_an_enormous_answer_is_refused_before_it_is_parsed():
    """Length first, so a pathological expression never reaches the parser."""
    result = sandbox.run_expression("9" * 4000, "1", None)
    assert result.ok is False


# --------------------------------------------------------------------------
# a right answer is never marked wrong
# --------------------------------------------------------------------------
@pytest.mark.parametrize("student,expected,tolerance", [
    ("1/2", "0.5", None),            # same number, written differently
    ("sqrt(16)", "4", None),
    ("(x-1)(x+1)", "x**2-1", None),  # implicit multiplication, algebraically equal
    ("2x+3", "3+x*2", None),
    ("pi/2", "1.5707963", 1e-5),     # a correct decimal for an exact value
    ("9.8", "9.81", 0.05),           # within the tolerance the instructor set
])
def test_equivalent_answers_are_accepted(student, expected, tolerance):
    result = sandbox.run_expression(student, expected, tolerance)
    assert result.ok is True
    assert result.equivalent is True


@pytest.mark.parametrize("student,expected,tolerance", [
    ("3x", "2x", None),
    ("12.4", "9.81", 0.05),
    ("pi/2", "1.5707963", None),  # no tolerance given: exact comparison stands
])
def test_wrong_answers_are_rejected(student, expected, tolerance):
    result = sandbox.run_expression(student, expected, tolerance)
    assert result.equivalent is False


def test_a_declared_form_is_not_mistaken_for_a_unit():
    """"exact fraction" is a student saying "I'll give 1/2, not 0.5". Reading it
    as a unit demanded units on a correct answer."""
    spec = {"outputs": [{"name": "x", "type": "exact fraction"}]}
    rows = math_check.check(spec, "answer: 1/2", {"expected_answer": "1/2"})
    assert all(r["passed"] for r in rows), [r["failure"] for r in rows if not r["passed"]]


def test_an_algebraic_answer_needs_no_units():
    rows = math_check.check({}, "answer: (x-1)(x+1)", {"expected_answer": "x**2-1"})
    assert all(r["passed"] for r in rows)


# --------------------------------------------------------------------------
# the failures it should find
# --------------------------------------------------------------------------
UNITS_SPEC = {"outputs": [{"name": "a", "unit": "m/s^2"}]}
RUBRIC = {"expected_answer": "9.81 m/s^2", "tolerance": 0.05}


def test_a_right_number_with_no_units_is_flagged_as_form_not_value():
    """A wrong unit on a right number is a different mistake from a wrong
    number, and the screen exists to say which."""
    rows = math_check.check(UNITS_SPEC, "answer: 9.8", RUBRIC)
    failed = [r for r in rows if not r["passed"]]

    assert len(failed) == 1
    assert failed[0]["key"] == "outputs"          # form, not value
    assert "no units" in failed[0]["failure"]


def test_a_wrong_value_is_reported_against_the_answer_itself():
    rows = math_check.check(UNITS_SPEC, "answer: 12.4 m/s^2", RUBRIC)
    failed = [r for r in rows if not r["passed"]]
    assert [r["key"] for r in failed] == ["answer"]


def test_a_wrong_unit_is_reported_separately_from_the_value():
    rows = math_check.check(UNITS_SPEC, "answer: 9.8 km", RUBRIC)
    keys = [r["key"] for r in rows if not r["passed"]]
    assert "outputs" in keys


def test_promising_an_exact_value_and_giving_a_decimal_is_drift():
    spec = {"outputs": [{"name": "x", "type": "exact fraction"}]}
    rows = math_check.check(spec, "answer: 0.5", {"expected_answer": "1/2"})
    failed = [r for r in rows if not r["passed"]]

    assert len(failed) == 1, [r["failure"] for r in failed]
    assert "exact" in failed[0]["failure"]


def test_prose_is_not_graded_as_mathematics():
    """Falling back to the last line meant grading a sentence of English and
    reporting two confusing failures about it."""
    rows = math_check.check(UNITS_SPEC, "I think it's about ten", RUBRIC)
    failed = [r for r in rows if not r["passed"]]

    assert len(failed) == 1
    assert "couldn't find a final answer" in failed[0]["failure"]


# --------------------------------------------------------------------------
# reading the student's working
# --------------------------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("x = 2\nanswer: 42", "42"),
    ("answer = 9.8 m/s^2", "9.8 m/s^2"),
    ("Final answer: 1/2", "1/2"),
    ("answer: 1\nanswer: 2", "2"),        # the last one is the one they meant
    ("lots of working\n12.5", "12.5"),    # no marker, but it looks like a value
    ("I'm not sure", ""),                 # no marker and not a value
    ("", ""),
])
def test_the_final_answer_is_found_in_the_working(text, expected):
    assert math_check.extract_answer(text) == expected


@pytest.mark.parametrize("answer,value,unit", [
    ("9.8 m/s^2", "9.8", "m/s^2"),
    ("40 km", "40", "km"),
    ("2x", "2x", ""),      # single letters are algebra, not units
    ("1/2", "1/2", ""),
])
def test_units_are_split_off_the_value(answer, value, unit):
    assert math_check.strip_units(answer) == (value, unit)


# --------------------------------------------------------------------------
# the rows look like every other checker's rows
# --------------------------------------------------------------------------
def test_rows_match_the_shape_the_rest_of_the_system_expects():
    """The classifier, dashboard, insights engine and student page all read
    these without knowing which subject produced them."""
    rows = math_check.check(UNITS_SPEC, "answer: 9.8", RUBRIC)
    required = {"kind", "key", "source", "input", "expected", "actual",
                "actual_type", "error", "timed_out", "stdout",
                "expectation_declared", "passed", "failure"}
    for row in rows:
        assert required <= set(row), required - set(row)
