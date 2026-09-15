"""The extraction guard — Rim's report: typing one vague sentence ticked the
first three rubric items, because the model filled inputs/outputs from the
assignment text rather than from anything the student said."""
from __future__ import annotations

from app.modules.spec_gate import _guard_extraction, field_satisfied

EMPTY = {"goal": "", "inputs": [], "outputs": [], "edge_cases": [],
         "examples": [], "explicit_exclusions": []}

INVENTED = {
    "goal": "remove duplicates from list",
    "inputs": [{"name": "items", "type": "list[int]", "constraints": ""}],
    "outputs": [{"name": "result", "type": "list[int]"}],
    "edge_cases": [], "examples": [], "explicit_exclusions": [],
}


def test_a_vague_first_message_records_only_the_goal():
    guarded = _guard_extraction(INVENTED, EMPTY, "remove duplicates from list")

    assert guarded["goal"] == "remove duplicates from list"
    assert guarded["inputs"] == [], "the student never named an input"
    assert guarded["outputs"] == [], "the student never said what comes back"


def test_an_input_the_student_named_survives():
    guarded = _guard_extraction(
        {**EMPTY, "inputs": [{"name": "n", "type": "int", "constraints": ""}]},
        EMPTY, "it takes an int n")
    assert guarded["inputs"][0]["name"] == "n"


def test_an_output_stated_with_a_verb_survives():
    guarded = _guard_extraction(
        {**EMPTY, "outputs": [{"name": "result", "type": "list"}]},
        EMPTY, "it returns a list")
    assert guarded["outputs"][0]["type"] == "list"


def test_an_unsaid_element_type_is_stripped_rather_than_invented():
    guarded = _guard_extraction(
        {**EMPTY, "outputs": [{"name": "result", "type": "list[int]"}]},
        EMPTY, "it returns a list")
    assert guarded["outputs"][0]["type"] == "list", "the student never said int"


def test_a_full_type_the_student_stated_is_kept():
    guarded = _guard_extraction(
        {**EMPTY, "outputs": [{"name": "result", "type": "list[int]"}]},
        EMPTY, "it returns a list of integers")
    assert guarded["outputs"][0]["type"] == "list[int]"


def test_an_invented_example_is_dropped():
    guarded = _guard_extraction(
        {**EMPTY, "examples": [{"input": "n=5", "output": "[0, 1, 1, 2, 3]"}]},
        EMPTY, "it returns a list of integers")
    assert guarded["examples"] == []


def test_an_example_the_student_wrote_survives():
    guarded = _guard_extraction(
        {**EMPTY, "examples": [{"input": "n=3", "output": "[0, 1, 1]"}]},
        EMPTY, "for example n=3 -> [0, 1, 1]")
    assert guarded["examples"][0]["input"] == "n=3"


def test_facts_accepted_in_an_earlier_turn_are_not_re_litigated():
    previous = {**EMPTY, "inputs": [{"name": "n", "type": "int", "constraints": ""}]}
    guarded = _guard_extraction(previous, previous, "something unrelated")
    assert guarded["inputs"] == previous["inputs"]


# ------------------------------------------------------- quality floor
def test_a_hollow_entry_does_not_count_as_answered():
    assert field_satisfied("inputs", [{"name": "", "type": ""}]) is False
    assert field_satisfied("outputs", [{"name": "result", "type": ""}]) is False
    assert field_satisfied("examples", [{"input": "n=3", "output": ""}]) is False
    assert field_satisfied("goal", "dedupe") is False


def test_a_real_entry_counts():
    assert field_satisfied("inputs", [{"name": "n", "type": "int"}]) is True
    assert field_satisfied("outputs", [{"name": "r", "type": "list"}]) is True
    assert field_satisfied("examples", [{"input": "n=3", "output": "[0]"}]) is True
    assert field_satisfied("goal", "returns the first n fibonacci numbers") is True
