"""The "I don't know" path, and the structural floor under the gate."""
from __future__ import annotations

from app.modules import spec_gate
from tests.test_spec_gate import post

FILLED = ("a function returning the first n fibonacci numbers, takes int n, "
          "returns a list of ints, n=3 -> [0, 1, 1]")


# ------------------------------------------------------- field mapping
def test_conditional_items_are_not_mistaken_for_field_checks():
    """Regression: 'empty_input' contains 'input', so the gate treated it as the
    inputs field and ticked it off as soon as an input was named."""
    assert spec_gate.required_field(
        {"id": "empty_input", "text": "must state the behavior when n = 0"}) is None
    assert spec_gate.required_field(
        {"id": "negative_input", "text": "must state the behavior for negative n"}) is None
    assert spec_gate.required_field(
        {"id": "order", "text": "must state whether the original order is preserved"}) is None


def test_field_items_still_map_to_their_field():
    assert spec_gate.required_field(
        {"id": "inputs", "text": "must name the input, its type and constraints"}) == "inputs"
    assert spec_gate.required_field(
        {"id": "examples", "text": "must give one concrete example"}) == "examples"
    assert spec_gate.required_field(
        {"id": "goal", "text": "must state what the function does"}) == "goal"


def test_an_explicit_field_on_the_rubric_item_wins():
    assert spec_gate.required_field({"id": "x", "text": "anything", "field": "outputs"}) == "outputs"
    assert spec_gate.required_field({"id": "x", "text": "give an example", "field": None}) is None


def test_structural_gaps_are_found_without_calling_a_model():
    rubric = [{"id": "examples", "field": "examples", "text": "must give an example"}]
    unmet = spec_gate.structurally_unmet({"examples": []}, rubric)
    assert [i["id"] for i in unmet] == ["examples"]
    assert spec_gate.structurally_unmet({"examples": [{"input": "n=1", "output": "[0]"}]},
                                        rubric) == []


# ------------------------------------------------------- the button
def test_dont_know_explains_the_question_without_answering_it(seeded):
    client, student_id, assignment_id = seeded
    asked = post(client, assignment_id, student_id, FILLED)
    gap_id = asked["gap"]["id"]

    result = client.post(f"/api/assignments/{assignment_id}/specification/dont-know",
                         json={"student_id": student_id}).json()

    assert result["explanation"], "it should explain what is being asked"
    assert result["question"], "and still end with a question the student answers"
    assert result["locked"] is False
    # The item is NOT skipped and the specification is NOT filled in for them.
    assert result["gap"]["id"] == gap_id
    assert result["content"] == asked["content"]


def test_dont_know_is_tracked_per_gap(seeded):
    client, student_id, assignment_id = seeded
    post(client, assignment_id, student_id, FILLED)
    for _ in range(2):
        client.post(f"/api/assignments/{assignment_id}/specification/dont-know",
                    json={"student_id": student_id})

    data = client.get(f"/api/assignments/{assignment_id}/specification",
                      params={"student_id": student_id}).json()
    assert sum(data["dont_know_counts"].values()) == 2


def test_dont_know_does_not_spend_an_ask(seeded):
    """Asking for help understanding the question is not the same as failing to
    answer it twice — it must not push you toward the override."""
    client, student_id, assignment_id = seeded
    before = post(client, assignment_id, student_id, FILLED)["asks_on_gap"]

    after = client.post(f"/api/assignments/{assignment_id}/specification/dont-know",
                        json={"student_id": student_id}).json()
    assert after["asks_on_gap"] == before


# ------------------------------------------------------- progress
def test_rubric_status_shows_what_is_still_outstanding(seeded):
    client, student_id, assignment_id = seeded
    result = post(client, assignment_id, student_id, FILLED)

    status = {s["id"]: s for s in result["rubric_status"]}
    assert status["goal"]["met"] is True
    assert status["examples"]["met"] is True
    assert status["empty_input"]["met"] is False
    assert status["empty_input"]["checkable"] is False
    assert result["items_met"] < result["items_total"]


def test_everything_is_met_once_locked(seeded):
    client, student_id, assignment_id = seeded
    for message in [FILLED, "for n = 0 return an empty list",
                    "negative n returns an empty list too"]:
        result = post(client, assignment_id, student_id, message)
        if result["locked"]:
            break

    assert result["locked"] is True
    assert result["items_met"] == result["items_total"]
    assert all(s["met"] for s in result["rubric_status"])


# ------------------------------------------------------- suggestions
def test_each_suggestion_carries_a_sentence_that_names_the_condition(seeded):
    """Regression: clicking a suggestion sent the bare label, so the gate
    recorded "return an empty result" with no trace of WHEN — leaving the
    Reflection Engine an edge case it cannot turn into a test."""
    client, student_id, assignment_id = seeded
    post(client, assignment_id, student_id, FILLED)

    result = client.post(f"/api/assignments/{assignment_id}/specification/dont-know",
                         json={"student_id": student_id}).json()

    assert result["options"], "a design decision should offer options"
    for option in result["options"]:
        assert option["label"] and option["answer"]
        assert len(option["answer"].split()) >= 4, "the answer must stand on its own"
    assert any("0" in o["answer"] for o in result["options"]), \
        "the option should name the condition it answers"


def test_pressing_i_dont_know_twice_does_not_repeat_itself(seeded):
    """Getting the identical paragraph back is what makes it feel stuck."""
    client, student_id, assignment_id = seeded
    post(client, assignment_id, student_id, FILLED)

    first = client.post(f"/api/assignments/{assignment_id}/specification/dont-know",
                        json={"student_id": student_id}).json()
    second = client.post(f"/api/assignments/{assignment_id}/specification/dont-know",
                         json={"student_id": student_id}).json()

    assert first["gap"]["id"] == second["gap"]["id"], "same question, still unanswered"
    assert second["explanation"] != first["explanation"], "it must try another angle"


def test_a_short_reply_is_recorded_with_the_question_it_answers(seeded):
    client, student_id, assignment_id = seeded
    asked = post(client, assignment_id, student_id, FILLED)
    assert "n = 0" in asked["question"] or "0" in asked["question"]

    answered = post(client, assignment_id, student_id, "an empty list")

    edge_cases = " ".join(answered["content"]["edge_cases"]).lower()
    assert "0" in edge_cases or "empty" in edge_cases
    assert answered["content"]["edge_cases"], "the answer must land somewhere"


def test_the_outstanding_question_is_remembered_between_turns(seeded, db):
    from app.models import Specification

    client, student_id, assignment_id = seeded
    post(client, assignment_id, student_id, FILLED)

    spec = db.query(Specification).filter(
        Specification.student_id == student_id).order_by(
        Specification.version.desc()).first()
    assert spec.last_question, "the gate must remember what it just asked"


def test_an_example_gap_is_offered_a_shape_not_a_finished_example(seeded):
    """Options should suit the question. For an example the student must supply
    the values — the most we can offer is the shape to fill in, or we would be
    answering it for them."""
    client, student_id, assignment_id = seeded
    post(client, assignment_id, student_id,
         "a function returning the first n fibonacci numbers, takes int n, returns a list of ints")

    result = client.post(f"/api/assignments/{assignment_id}/specification/dont-know",
                         json={"student_id": student_id}).json()

    assert result["gap"]["id"] == "examples"
    assert result["gap"]["kind"] == "example"
    assert len(result["options"]) == 1
    answer = result["options"][0]["answer"]
    assert "..." in answer, "it must be a blank to fill, not a worked example"


def test_a_template_sent_back_unedited_is_not_stored_as_an_example(seeded):
    """Otherwise the Reflection Engine inherits a test case it cannot run."""
    client, student_id, assignment_id = seeded
    post(client, assignment_id, student_id,
         "a function returning the first n fibonacci numbers, takes int n, returns a list of ints")

    result = post(client, assignment_id, student_id, "n=... -> ...")

    assert result["content"]["examples"] == []
    assert result["locked"] is False


def test_a_type_gap_is_offered_candidate_types(seeded):
    client, student_id, assignment_id = seeded
    result = post(client, assignment_id, student_id, "remove duplicates from a list")
    assert result["gap"]["id"] == "inputs"

    helped = client.post(f"/api/assignments/{assignment_id}/specification/dont-know",
                         json={"student_id": student_id}).json()

    assert helped["gap"]["kind"] == "type"
    assert len(helped["options"]) >= 2, "candidate types are a real choice"
    assert all(o["answer"] for o in helped["options"])


def test_a_decision_gap_still_gets_options(seeded):
    client, student_id, assignment_id = seeded
    post(client, assignment_id, student_id, FILLED)

    result = client.post(f"/api/assignments/{assignment_id}/specification/dont-know",
                         json={"student_id": student_id}).json()

    assert result["gap"]["id"] == "empty_input"
    assert result["options"], "a real design choice should offer options"


def test_a_fill_in_shape_is_not_prefixed_with_the_question(seeded):
    """A template is a shape to edit, not a sentence — prefixing it just makes
    the student delete boilerplate before they can answer."""
    client, student_id, assignment_id = seeded
    post(client, assignment_id, student_id,
         "a function returning the first n fibonacci numbers, takes int n, returns a list of ints")

    result = client.post(f"/api/assignments/{assignment_id}/specification/dont-know",
                         json={"student_id": student_id}).json()

    answer = result["options"][0]["answer"]
    assert answer.startswith("n=") or answer.startswith("items="), answer
    assert "must give" not in answer


# ------------------------------------------------------- options must be complete answers
def test_a_type_option_answers_the_whole_rubric_item(seeded):
    """Rim's report: the question asked for a NAME and a TYPE, the offered option
    gave only the type — so clicking our own suggestion was rejected."""
    client, student_id, assignment_id = seeded
    client.get("/api/assignments").json()
    post(client, assignment_id, student_id, "return the first n fibonacci numbers")

    helped = client.post(f"/api/assignments/{assignment_id}/specification/dont-know",
                         json={"student_id": student_id}).json()
    assert helped["gap"]["id"] == "inputs"

    for option in helped["options"]:
        assert "called" in option["answer"] or "named" in option["answer"], option


def test_clicking_a_type_option_actually_satisfies_the_gate(seeded):
    """The real test: send the suggestion back verbatim and the gate must move on."""
    client, student_id, assignment_id = seeded
    post(client, assignment_id, student_id, "return the first n fibonacci numbers")
    helped = client.post(f"/api/assignments/{assignment_id}/specification/dont-know",
                         json={"student_id": student_id}).json()

    answer = helped["options"][0]["answer"]
    result = post(client, assignment_id, student_id, answer)

    assert result["content"]["inputs"], f"clicking our own option must work: {answer!r}"
    assert (result["gap"] or {}).get("id") != "inputs", "the gate should have moved on"


def test_parameter_names_come_from_the_starter_code():
    from app.modules.spec_gate import signature_params

    assert signature_params("def dedupe(items):\n    pass") == ["items"]
    assert signature_params("def fib(n):\n    pass") == ["n"]
    assert signature_params("def f(a, b=2, *rest):") == ["a", "b", "rest"]
    assert signature_params(None) == []
