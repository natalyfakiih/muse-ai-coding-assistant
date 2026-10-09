"""How a student's example becomes a call — Rim's report on dedupe.

Her locked specification held `example: "1,1,2" -> "1,2"` and the edge case
"empty list returns an empty list". Correct code:

    def dedupe(items):
        result = []
        for item in items:
            if item not in result:
                result.append(item)
        return result

failed both. `"1,1,2"` was parsed as THREE positional arguments, so the runner
called `dedupe(1, 1, 2)`; and `"1,2"` literal-evals to the tuple `(1, 2)`, which
never compares equal to the list `[1, 2]`.

The damage was not the red X. She rewrote a correct function into

    def dedupe(*args, items=None): ...  return ",".join(map(str, result))

to satisfy the harness — deforming right code to fit a broken grader, which is
the precise opposite of what this tool exists to do. These tests exist so that
cannot happen again.
"""
from __future__ import annotations

from app.modules import reflection, sandbox

CLEAN = '''def dedupe(items):
    result = []
    for item in items:
        if item not in result:
            result.append(item)
    return result
'''

VARIADIC = '''def dedupe(*args, items=None):
    return list(args)
'''


# --------------------------------------------------------------------------
# reading the example
# --------------------------------------------------------------------------
def test_a_bracketless_list_is_one_argument_for_a_one_parameter_function():
    """Verbatim from her specification."""
    assert sandbox.parse_call_args("1,1,2", arity=1) == ([[1, 1, 2]], {})


def test_the_same_text_stays_three_arguments_for_a_three_parameter_function():
    assert sandbox.parse_call_args("1,1,2", arity=3) == ([1, 1, 2], {})


def test_a_variadic_entry_point_is_never_reshaped():
    """*args accepts any number, so there is no arity to reconcile against."""
    assert sandbox.entry_arity(VARIADIC, "dedupe") is None
    assert sandbox.parse_call_args("1,1,2", arity=None) == ([1, 1, 2], {})


def test_explicit_brackets_are_left_alone():
    assert sandbox.parse_call_args("[1, 1, 2]", arity=1) == ([[1, 1, 2]], {})


def test_keyword_form_is_left_alone():
    assert sandbox.parse_call_args("items=[]", arity=1) == ([], {"items": []})


def test_arity_is_read_from_the_students_own_code():
    assert sandbox.entry_arity(CLEAN, "dedupe") == 1
    assert sandbox.entry_arity("def f(a, b, c=1):\n    pass\n", "f") == 3
    assert sandbox.entry_arity("def other(x):\n    pass\n", "missing") is None
    assert sandbox.entry_arity("def broken(:\n", "broken") is None


# --------------------------------------------------------------------------
# comparing the result
# --------------------------------------------------------------------------
def test_a_comma_separated_expectation_matches_the_list_it_describes():
    """"1,2" parses as a tuple. The student meant the sequence; the container
    type is separately checked against the declared output type."""
    assert sandbox.matches("[1, 2]", "1,2") is True
    assert sandbox.matches("(1, 2)", "[1, 2]") is True


def test_sequence_leniency_does_not_leak_into_other_comparisons():
    assert sandbox.matches("'1,2'", "1,2") is False      # a string is not a sequence of ints
    assert sandbox.matches("[1, 2]", "[2, 1]") is False  # order still matters
    assert sandbox.matches("[1, 2]", "[1, 2, 3]") is False
    assert sandbox.matches("True", "1") is False         # the bool guard still holds


# --------------------------------------------------------------------------
# end to end, against her specification
# --------------------------------------------------------------------------
def test_her_code_passes_her_own_example():
    result = sandbox.run_case(CLEAN, "dedupe", "1,1,2")
    assert result.error is None, result.error
    assert result.value_repr == "[1, 2]"
    assert sandbox.matches(result.value_repr, "1,2") is True


def test_her_code_passes_her_own_empty_list_edge_case():
    result = sandbox.run_case(CLEAN, "dedupe", "items=[]")
    assert result.error is None, result.error
    assert sandbox.matches(result.value_repr, "[]") is True


# --------------------------------------------------------------------------
# the failure message
# --------------------------------------------------------------------------
def test_a_mismatch_that_reads_identically_names_the_types():
    """"expected '1,2', got '1,2'" is a message this really produced."""
    returned = sandbox.RunResult(ok=True, value_repr="'1,2'", value_type="str")
    message = reflection._mismatch("1,2", returned)

    assert "str" in message and "tuple" in message
    assert "different type" in message


def test_an_ordinary_mismatch_stays_short():
    returned = sandbox.RunResult(ok=True, value_repr="[2, 1]", value_type="list")
    assert reflection._mismatch("[1, 2]", returned) == "expected [1, 2], got [2, 1]"
