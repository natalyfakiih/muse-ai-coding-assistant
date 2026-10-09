"""The goal gap — Rim's report on the dedupe assignment: she wrote "We need to
remove duplicates", the gate asked her about input and output types, she
answered those correctly, and it told her that didn't settle it. Then it asked
for "a one-sentence description" of the thing she had already described.

Two separate faults, both reproduced here:

1. The extractor compressed her sentence to "remove duplicates" — two words,
   under the substance floor — so `goal` never became satisfied no matter what
   she typed next.
2. The clarifying-question model was handed the `goal` gap and asked about
   inputs and outputs instead. Answering the question on screen therefore could
   not possibly close the gap being scored, which is the worst failure mode this
   gate has: the student is right and is told she is wrong.
"""
from __future__ import annotations

from app.modules.spec_gate import (
    _canned_question,
    _guard_goal,
    _question_addresses_gap,
    field_satisfied,
)

GOAL_GAP = {"id": "goal", "field": "goal",
            "text": "must state in one sentence what the function does"}
DEDUPE_STARTER = "def dedupe(items):\n    # your code here\n    pass\n"


# --------------------------------------------------------------------------
# 1. the compressed goal
# --------------------------------------------------------------------------
def test_her_own_sentence_survives_the_models_compression():
    """Verbatim from her session: the model's two-word summary fails the floor,
    so the sentence she actually typed is what gets stored."""
    assert field_satisfied("goal", "remove duplicates") is False

    kept = _guard_goal("remove duplicates", "", "We need to remove duplicates")

    assert kept == "We need to remove duplicates"
    assert field_satisfied("goal", kept) is True


def test_a_goal_that_already_passes_is_left_exactly_as_extracted():
    """The guard is a floor, not a rewrite: a good extraction is not touched."""
    assert _guard_goal("returns the first n fibonacci numbers", "",
                       "i want a function that returns the first n fibonacci numbers"
                       ) == "returns the first n fibonacci numbers"


def test_a_non_answer_is_never_promoted_into_the_goal():
    """"I don't know" is three words and would clear the floor on length alone.
    Storing it would unlock the gate on a student who just said she's stuck."""
    assert _guard_goal("remove duplicates", "", "I don't know") == "remove duplicates"
    assert _guard_goal("remove duplicates", "", "not sure really") == "remove duplicates"


def test_an_unrelated_sentence_is_not_adopted_as_the_goal():
    """The promoted sentence has to be the one the model drew its goal from."""
    assert _guard_goal("sort the list", "", "my name is Rim and this is my project"
                       ) == "sort the list"


def test_the_right_sentence_is_picked_out_of_several():
    message = ("Hi again. I think the function should remove duplicate values "
               "from a list. Let me know if that's enough.")
    assert _guard_goal("remove duplicates", "", message) == (
        "I think the function should remove duplicate values from a list.")


# --------------------------------------------------------------------------
# 2. the question that belonged to a different gap
# --------------------------------------------------------------------------
def test_a_question_about_inputs_is_rejected_for_the_goal_gap():
    """Verbatim from her session. Answering this correctly left `goal` unmet,
    so she was told a correct answer didn't settle it."""
    asked = ("What is the input type and expected output of the function to "
             "remove duplicates?")
    assert _question_addresses_gap(asked, GOAL_GAP) is False


def test_a_real_goal_question_is_kept():
    for asked in ("In your own words, what should this function do?",
                  "Can you describe in one sentence what it does?",
                  "What is this function's purpose?"):
        assert _question_addresses_gap(asked, GOAL_GAP) is True


def test_an_unmarked_question_is_kept_rather_than_second_guessed():
    """The check only fires on a question that plainly belongs to another field.
    Trading good phrasing for a canned question on a maybe is a bad deal."""
    assert _question_addresses_gap("What should this function accomplish?",
                                   GOAL_GAP) is True


def test_a_semantic_gap_is_never_second_guessed():
    """"must state the behavior for an empty list" has no field, so there is
    nothing to check the question against."""
    gap = {"id": "empty_input", "field": None,
           "text": "must state the behavior for an empty list"}
    assert _question_addresses_gap("Anything at all, really?", gap) is True


def test_the_canned_question_uses_the_real_signature():
    goal = _canned_question(GOAL_GAP, DEDUPE_STARTER)
    assert "dedupe" in goal
    assert "one sentence" in goal.lower()

    inputs = _canned_question({"id": "inputs", "field": "inputs",
                               "text": "must name the input list"}, DEDUPE_STARTER)
    assert "items" in inputs


def test_the_canned_question_gives_nothing_away():
    """It asks; it does not answer. None of these may name the algorithm."""
    for field in ("goal", "inputs", "outputs", "examples"):
        asked = _canned_question({"id": field, "field": field, "text": ""},
                                 DEDUPE_STARTER).lower()
        assert "duplicate" not in asked
        assert "set(" not in asked
