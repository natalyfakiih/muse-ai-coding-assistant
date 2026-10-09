"""Phase 3 for mathematics.

The Python checker runs a program and diffs its behaviour against the locked
specification. There is no program here, so the equivalent is: the student
declared what their answer would *be* — its form, its units, roughly its size —
and then produced one. This compares the two, and separately compares the
answer to the one the instructor set.

That keeps the three categories meaning what they mean elsewhere:

  drift     the answer's FORM differs from what they said it would be
            (no units when they declared units, a decimal when they said exact)
  bug       they followed their own stated method and got the wrong value
  revision  they changed their approach on purpose and said so in the log

Every row this produces has the same shape as the Python checker's rows, so the
classifier, the dashboard, the insights engine and the student's own page all
work on it without knowing which subject it came from.
"""
from __future__ import annotations

import re

from app.modules import sandbox

# "answer: 42 m/s" / "answer = 42" / "final answer 42". Students write the
# working above and the answer at the end, so the marker is what we look for
# first and the last non-empty line is the fallback.
_ANSWER_LINE = re.compile(
    r"^\s*(?:final\s+)?answer\s*[:=]?\s*(.+?)\s*$", re.I | re.M)

# A unit suffix on the end of a numeric answer: "9.8 m/s^2", "40 km".
_UNIT_TAIL = re.compile(r"[-+0-9.,/^*() \t]*([A-Za-z][A-Za-z/^·\d]*)\s*$")

# Words a student uses when declaring the SHAPE of an answer rather than a value.
_EXACT_WORDS = re.compile(r"\bexact|\bfraction|\bsurd|\bsimplif|\bin terms of\b", re.I)
_DECIMAL_WORDS = re.compile(r"\bdecimal|\b\d+\s*(?:d\.?p\.?|significant|s\.?f\.?)", re.I)


def extract_answer(submission: str) -> str:
    """The student's final answer, out of their working."""
    text = str(submission or "")
    matches = _ANSWER_LINE.findall(text)
    if matches:
        return matches[-1].strip()
    # No marker. Fall back to the last line only if it could plausibly BE an
    # answer; otherwise say we found none, rather than grading a sentence of
    # English and reporting two confusing failures about it.
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    last = lines[-1]
    return last if re.search(r"[0-9]|[+\-*/^=]", last) else ""


def strip_units(answer: str) -> tuple[str, str]:
    """Split "9.8 m/s^2" into the value and the unit, so each can be judged on
    its own. A wrong unit on a right number is a different mistake from a wrong
    number, and saying so is the whole point of this screen."""
    answer = str(answer or "").strip()
    match = _UNIT_TAIL.search(answer)
    if not match:
        return answer, ""
    unit = match.group(1)
    # Single letters are almost always algebra (x, n, t), not units.
    if len(unit) == 1 and unit.isalpha():
        return answer, ""
    value = answer[:match.start(1)].strip()
    return (value or answer), unit


# Words that describe the SHAPE of an answer, never its units. "exact fraction"
# in an outputs field is a student saying "I'll give you 1/2, not 0.5" — reading
# it as a unit makes the checker demand units on a correct answer, which is the
# one thing this system must never do.
_NOT_A_UNIT = re.compile(
    r"exact|fraction|surd|decimal|simplif|number|numeric|value|integer|"
    r"\bint\b|\bfloat\b|expression|terms of|answer", re.I)


def declared_units(content: dict) -> str:
    """Units the student committed to in their specification, if any."""
    for output in content.get("outputs") or []:
        if not isinstance(output, dict):
            continue
        for field in ("unit", "units", "type"):
            value = str(output.get(field) or "").strip()
            if not value or _NOT_A_UNIT.search(value):
                continue
            # A unit is short: "m/s^2", "kg", "degrees". A sentence is not.
            if len(value.split()) <= 2:
                return value
    return ""


def _row(*, kind, key, source, expected, actual, passed, failure, detail=None) -> dict:
    """The same row shape the Python checker emits, so everything downstream
    stays subject-agnostic."""
    return {
        "kind": kind,
        "key": key,
        "source": source,
        "input": detail or "",
        "expected": expected,
        "actual": actual,
        "actual_type": "expression",
        "error": None,
        "timed_out": False,
        "stdout": "",
        "expectation_declared": expected is not None,
        "passed": passed,
        "failure": failure,
    }


def check(content: dict, submission: str, rubric: dict) -> list[dict]:
    """content = the student's locked specification, submission = their working,
    rubric = the assignment (which holds the instructor's expected answer)."""
    rows: list[dict] = []
    expected_raw = str((rubric or {}).get("expected_answer") or "").strip()
    tolerance = (rubric or {}).get("tolerance")

    given = extract_answer(submission)
    if not given:
        return [_row(kind="answer", key="answer", source="your final answer",
                     expected=expected_raw or None, actual=None, passed=False,
                     failure="I couldn't find a final answer. End with a line "
                             "like 'answer: 9.8 m/s^2'.")]

    value, unit = strip_units(given)
    expected_value, expected_unit = strip_units(expected_raw)

    # 1. the value itself
    if expected_raw:
        result = sandbox.run_expression(value, expected_value, tolerance)
        if not result.ok:
            rows.append(_row(
                kind="answer", key="answer", source="your final answer",
                expected=expected_value, actual=value, passed=False,
                failure=f"I couldn't read that as a value: {result.error}"))
        elif result.equivalent:
            rows.append(_row(kind="answer", key="answer", source="your final answer",
                             expected=expected_value, actual=value,
                             passed=True, failure=None,
                             detail=f"matched {result.mode}ly"))
        else:
            rows.append(_row(
                kind="answer", key="answer", source="your final answer",
                expected=expected_value, actual=value, passed=False,
                failure=f"expected {expected_value}, got {value}"))

    # 2. the units, judged separately from the value
    promised = declared_units(content) or expected_unit
    if promised:
        if not unit:
            rows.append(_row(
                kind="form", key="outputs", source="the form you declared",
                expected=promised, actual=value, passed=False,
                failure=f"your specification says the answer is in {promised}, "
                        f"but your answer has no units on it"))
        elif expected_unit and unit.lower() != expected_unit.lower():
            rows.append(_row(
                kind="form", key="outputs", source="the form you declared",
                expected=expected_unit, actual=unit, passed=False,
                failure=f"expected the answer in {expected_unit}, got {unit}"))

    # 3. exact vs decimal, when they committed to one
    wanted_exact = any(_EXACT_WORDS.search(str(o)) for o in (content.get("outputs") or []))
    looks_decimal = bool(re.search(r"\d\.\d", value))
    if wanted_exact and looks_decimal:
        rows.append(_row(
            kind="form", key="outputs", source="the form you declared",
            expected="an exact value", actual=value, passed=False,
            failure="you said you'd give an exact value and this is a decimal "
                    "approximation"))

    return rows
