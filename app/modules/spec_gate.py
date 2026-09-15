"""Step 1 — Specification Gate (Section 3.1).

A turn-based dialogue that runs before the code editor unlocks. Per student
message, in this order: extract what they said into the structured shape, then
check the assignment rubric for the highest-priority item still unanswered and
ask exactly one question about it.

Completeness is rubric-driven, not "is this clear?" — that is what makes the
unlock a checkable condition instead of a vibe judgement. The gate unlocks when
all rubric items are met, or when a gap has been asked about twice and the
student confirms their answer stands (which is what stops it becoming an
infuriating gatekeeper on a genuinely ambiguous edge case).

Volume here is a handful of calls per assignment, so free-tier Mistral covers it
comfortably; Groq fallback is rarely exercised on this path.
"""
from __future__ import annotations

import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.llm import CLASSIFICATION, DIALOGUE, generate_structured
from app.llm import prompts
from app.models import Assignment, Specification

EMPTY_SPEC: dict = {
    "goal": "",
    "inputs": [],
    "outputs": [],
    "edge_cases": [],
    "examples": [],
    "explicit_exclusions": [],
}

SPEC_FIELDS = tuple(EMPTY_SPEC.keys())


# --------------------------------------------------------------------------
# The three interface functions from the spec's Table 2
# --------------------------------------------------------------------------
def extract_fields(message: str, current_spec: dict,
                   answering_question: str | None = None) -> dict:
    """student message + current Specification JSON -> updated Specification JSON.

    `answering_question` is the question the student was just asked. Without it a
    reply like "an empty list" gets stored bare, losing the condition it applies
    to — which then leaves the Reflection Engine with an edge case it cannot turn
    into a test.
    """
    system, schema = prompts.EXTRACT_FIELDS
    payload = json.dumps(
        {"student_message": message, "current_specification": current_spec,
         "answering_question": answering_question},
        ensure_ascii=False,
    )
    raw = generate_structured(system, payload, schema, task="extract_fields", tier=CLASSIFICATION)
    return _guard_extraction(_normalise_spec(raw, fallback=current_spec),
                             current_spec, message)


# --------------------------------------------------------------------------
# Extraction guard
#
# A classification-tier model reads "remove duplicates from a list" and happily
# fills in inputs [{items, list[int]}] and outputs [{result, list[int]}] — plausible,
# and lifted from the assignment rather than from the student. The gate then
# ticks three rubric items the student never answered, which is the exact
# failure the rubric is supposed to prevent. Prompting alone doesn't hold here,
# so the model's output is checked against the student's actual words.
# --------------------------------------------------------------------------
_TYPE_SYNONYMS = {
    "int": ("int", "integer", "whole number", "number", "digit"),
    "float": ("float", "decimal", "real", "number"),
    "str": ("str", "string", "text", "word", "character", "char"),
    "bool": ("bool", "boolean", "true", "false", "flag"),
    "list": ("list", "array", "sequence", "collection", "series"),
    "dict": ("dict", "dictionary", "map", "mapping", "key"),
    "set": ("set", "unique"),
    "tuple": ("tuple", "pair"),
    "none": ("none", "null", "nothing"),
    "any": ("any", "anything"),
}

_RETURN_SIGNAL = re.compile(
    r"\breturns?\b|\breturned\b|\bgives?\b|\boutputs?\b|\bproduces?\b|\bhands? back\b"
    r"|\bresults? in\b|->|=>|\byields?\b", re.I)
_INPUT_SIGNAL = re.compile(
    r"\btakes?\b|\baccepts?\b|\breceives?\b|\bgiven\b|\binputs?\b|\bparameters?\b"
    r"|\barguments?\b|\bpassed\b|\bcalled with\b", re.I)


def _mentions(word: str, haystack: str) -> bool:
    word = (word or "").strip().lower()
    if not word:
        return False
    return re.search(rf"\b{re.escape(word)}", haystack) is not None


def _type_supported(type_name: str, haystack: str) -> str:
    """Keep only the parts of a declared type the student actually said.

    "list[int]" claimed from a message that never mentions integers becomes
    "list"; a type with no support at all becomes empty.
    """
    raw = str(type_name or "").strip().lower()
    if not raw:
        return ""
    tokens = [t for t in re.findall(r"[a-z_]+", raw) if t]
    supported = [
        t for t in tokens
        if any(_mentions(word, haystack) for word in _TYPE_SYNONYMS.get(t, (t,)))
    ]
    if not supported:
        return ""
    if len(supported) == len(tokens):
        return str(type_name).strip()
    return supported[0]  # container survives, the unsaid parameter does not


def _guard_extraction(updated: dict, previous: dict, message: str) -> dict:
    """Drop anything the model added that the student's words don't support."""
    previous = previous or {}
    text = f"{message or ''}".lower()
    # Facts already accepted in an earlier turn stay accepted.
    accepted = json.dumps(previous, ensure_ascii=False).lower()
    haystack = f"{text} {accepted}"

    def kept_before(entry: dict, field: str) -> bool:
        return any(entry == old for old in (previous.get(field) or []))

    guarded = dict(updated)

    inputs = []
    for entry in updated.get("inputs") or []:
        if not isinstance(entry, dict):
            continue
        if kept_before(entry, "inputs"):
            inputs.append(entry)
            continue
        named = _mentions(entry.get("name", ""), text)
        # An input needs either the student naming it, or them saying the
        # function takes something — not merely the word "list" in a sentence
        # about what the function does.
        if not named and not _INPUT_SIGNAL.search(message or ""):
            continue
        entry = dict(entry)
        entry["type"] = _type_supported(entry.get("type", ""), haystack)
        if not entry.get("name") and not entry.get("type"):
            continue
        inputs.append(entry)
    guarded["inputs"] = inputs

    outputs = []
    for entry in updated.get("outputs") or []:
        if not isinstance(entry, dict):
            continue
        if kept_before(entry, "outputs"):
            outputs.append(entry)
            continue
        # Students state an output with a verb. Without one, the model is
        # guessing from the assignment.
        if not _RETURN_SIGNAL.search(message or ""):
            continue
        entry = dict(entry)
        entry["type"] = _type_supported(entry.get("type", ""), haystack)
        if not entry.get("type"):
            continue
        outputs.append(entry)
    guarded["outputs"] = outputs

    examples = []
    for entry in updated.get("examples") or []:
        if not isinstance(entry, dict):
            continue
        if kept_before(entry, "examples"):
            examples.append(entry)
            continue
        # An invented example becomes a test the student never agreed to. Check
        # each side on its own: "n=3 -> [0, 1, 1]" has the arrow between them, so
        # the two halves are never contiguous in the source text.
        flat = re.sub(r"\s+", "", haystack)
        sides = [re.sub(r"\s+", "", str(entry.get(k, ""))).lower() for k in ("input", "output")]
        if any(side and side not in flat for side in sides):
            continue
        # A fill-in-the-blanks template sent back unedited is not an example;
        # storing it would hand the Reflection Engine an unrunnable test case.
        if _PLACEHOLDER.search(f"{entry.get('input', '')} {entry.get('output', '')}"):
            continue
        examples.append(entry)
    guarded["examples"] = examples

    return guarded


# Which specification field a rubric item is about, when that can be decided in
# code. An item mapped here is UNMET whenever its field is empty — no model gets
# a say. Set "field" explicitly on a rubric item to override the guess.
_FIELD_KEYWORDS = (
    (r"\bexamples?\b", "examples"),
    (r"\bedge cases?\b", "edge_cases"),
    (r"\binputs?\b", "inputs"),
    (r"\boutputs?\b", "outputs"),
    (r"\breturn(s|ed|ing)?\b", "outputs"),
    (r"\bgoals?\b", "goal"),
    (r"what the (function|program|code) does", "goal"),
)

# An item describing behaviour under a CONDITION ("must state the behavior when
# n = 0") is a semantic item, not a field-presence check — no field being
# non-empty proves it was answered. Matching these first is what stops
# "empty_input" being treated as the "inputs" field just because the word
# "input" appears in its name.
_SEMANTIC_MARKERS = re.compile(
    r"\bbehaviou?r\b|\bwhen\b|\bif\b|\bwhether\b|\bfor negative\b|\bpreserved?\b", re.I
)


def required_field(item: dict) -> str | None:
    """The spec field this rubric item requires, or None if it's a semantic item
    that only a model can judge.

    An explicit "field" on the rubric item always wins — including an explicit
    null, which marks the item semantic. The keyword fallback is deliberately
    conservative: guessing wrong in the "structural" direction silently ticks off
    a question that was never asked.
    """
    if "field" in item:
        return item["field"] or None

    text = str(item.get("text", ""))
    if _SEMANTIC_MARKERS.search(text):
        return None
    # Match on the text only. Rubric ids are short labels chosen by the
    # instructor ("empty_input") and are a trap for substring matching.
    matches = [(len(pattern), field) for pattern, field in _FIELD_KEYWORDS
               if re.search(pattern, text, re.I)]
    return max(matches)[1] if matches else None


def field_satisfied(field: str, value) -> bool:
    """Is this field answered concretely enough to implement from?

    Presence is not enough: `[{"name": "", "type": ""}]` is an empty answer
    wearing a list's clothes, and an example missing its expected output can't
    become a test.
    """
    if not value:
        return False
    if field == "goal":
        return len(str(value).split()) >= 3
    if field == "inputs":
        return any(isinstance(i, dict) and i.get("name") and i.get("type") for i in value)
    if field == "outputs":
        return any(isinstance(o, dict) and o.get("type") for o in value)
    if field == "examples":
        return any(isinstance(e, dict) and str(e.get("input", "")).strip()
                   and str(e.get("output", "")).strip() for e in value)
    return True


def structurally_unmet(spec: dict, rubric: list[dict]) -> list[dict]:
    """Rubric items whose required field isn't concretely answered. Checked in
    Python, so a specification with no examples can never be declared complete
    — and so a thin, model-invented answer doesn't tick the box either."""
    unmet = []
    for item in rubric:
        field = required_field(item)
        if field and not field_satisfied(field, (spec or {}).get(field)):
            unmet.append(item)
    return unmet


def next_gap(spec: dict, rubric: list[dict]) -> dict | None:
    """Specification JSON + Assignment rubric -> highest-priority unmet item, or None.

    Two layers. Structural items (goal, inputs, outputs, examples) are decided in
    code — an empty field is unmet, full stop. Only the semantic items are put to
    the model, and its answer is validated against the rubric before it is
    trusted. Without this floor a weak classification model can return
    `gap: null` on a near-empty specification and unlock the editor, which is
    exactly the failure the rubric-driven design is meant to prevent.
    """
    if not rubric:
        return None

    structural = structurally_unmet(spec, rubric)
    if structural:
        item = structural[0]  # rubric_items is already priority-sorted
        return {"id": str(item.get("id") or item.get("text", "")[:60]),
                "text": item.get("text", ""), "source": "structural",
                "field": required_field(item), "kind": gap_kind_for(item)}

    semantic = [item for item in rubric if not required_field(item)]
    if not semantic:
        return None

    system, schema = prompts.NEXT_GAP
    payload = json.dumps({"rubric": semantic, "current_specification": spec},
                         ensure_ascii=False)
    raw = generate_structured(system, payload, schema, task="next_gap", tier=CLASSIFICATION)
    gap = raw.get("gap")
    if not gap:
        return None
    if isinstance(gap, str):
        gap = {"id": gap[:60], "text": gap}
    if not gap.get("text") and not gap.get("id"):
        return None

    # Trust but verify: the model must name an item that is actually in the
    # rubric. A hallucinated requirement would be an infuriating gate.
    by_id = {str(i.get("id")): i for i in semantic}
    matched = by_id.get(str(gap.get("id")))
    if matched is None:
        text = str(gap.get("text", "")).lower()
        matched = next((i for i in semantic
                        if text and text in str(i.get("text", "")).lower()), None)
    if matched is None:
        return None

    return {"id": str(matched.get("id")), "text": matched.get("text", ""),
            "source": "model", "field": required_field(matched),
            "kind": gap_kind_for(matched)}


def explain_gap(gap: dict, spec: dict, assignment_prompt: str = "",
                attempt: int = 1, gap_kind: str = "decision",
                starter_code: str | None = None) -> dict:
    """The "I don't know" path.

    Scaffolding, not an answer: it explains what the question is asking and, for
    a genuine design decision, names legitimate options — the student still picks.
    Dialogue tier, because a bad explanation here is worse than none.

    `gap_kind` matters more than it looks. Asked to explain "give me a concrete
    example" while the assignment text mentions empty lists, a model will happily
    explain the empty-list decision instead — answering a question nobody asked,
    and offering options for something that has no options. A gap needing
    specific content gets no options at all, enforced here rather than hoped for
    in the prompt.
    """
    system, schema = prompts.EXPLAIN_GAP
    payload = json.dumps(
        {
            "gap": gap,
            "gap_kind": gap_kind,
            "current_specification": spec,
            # Labelled as background so it isn't mistaken for the question.
            "assignment_prompt_background_only": assignment_prompt,
            "attempt": attempt,
            # The signature the student is going to fill in; name inputs after it.
            "parameter_names": signature_params(starter_code),
        },
        ensure_ascii=False,
    )
    raw = generate_structured(system, payload, schema, task="explain_gap", tier=DIALOGUE)
    options = ([] if gap_kind == "statement"
               else _normalise_options(raw.get("options"), gap,
                                       signature_params(starter_code)))
    return {
        "explanation": (raw.get("explanation") or "").strip(),
        "options": options,
        "question": (raw.get("question") or "").strip(),
    }


_NAMES_IT = re.compile(r"\bcalled\b|\bnamed\b|\bparameter\b|\bargument\b", re.I)


def signature_params(starter_code: str | None) -> list[str]:
    """Parameter names from the assignment's starter code, e.g. `def dedupe(items)`.

    The student is going to fill in that exact signature, so this is the name
    their specification should use — no reason to invent a different one.
    """
    match = re.search(r"def\s+\w+\s*\(([^)]*)\)", starter_code or "")
    if not match:
        return []
    params = []
    for part in match.group(1).split(","):
        name = part.split(":")[0].split("=")[0].strip()
        if name and name not in ("self", "cls", "*", "**"):
            params.append(name.lstrip("*"))
    return params


def _normalise_options(options, gap: dict, param_names: list[str] | None = None) -> list[dict]:
    """Each option carries a ready-to-send sentence as well as a short label.

    Sending the bare label loses the condition it answers — "return an empty
    list" recorded against no question is useless to the Reflection Engine — so
    an option without its own sentence gets the question prepended.

    An option must also be a COMPLETE answer to the rubric item it's offered
    for. "must name the input list and the type of its elements" asks for two
    things, so an option saying only "a list of integers" gets rejected when the
    student clicks it — which is infuriating, and entirely our fault. If the
    item wants a name and the option hasn't got one, the real parameter name
    from the starter code is added.
    """
    if not isinstance(options, list):
        return []
    wants_name = (gap.get("field") == "inputs"
                  and re.search(r"\bnames?\b", str(gap.get("text", "")), re.I))
    param = (param_names or ["items"])[0] if param_names else "items"
    out = []
    for option in options[:4]:
        if isinstance(option, dict):
            label = str(option.get("label") or option.get("answer") or "").strip()
            answer = str(option.get("answer") or "").strip()
        else:
            label = answer = str(option).strip()
        if not label:
            continue
        if not answer:
            answer = label
        # A short answer gets the question glued on so it still means something
        # once the question scrolls away — but a fill-in shape is not a sentence,
        # and prefixing it just leaves the student deleting boilerplate.
        is_template = bool(_PLACEHOLDER.search(answer))
        if not is_template and len(answer.split()) < 6 and gap.get("text"):
            answer = f"{gap['text'].replace('must state ', '').strip()}: {answer}"
        if wants_name and not is_template and not _NAMES_IT.search(answer):
            answer = f"{answer.rstrip('.')} called {param}."
        out.append({"label": label[:80], "answer": answer[:400]})
    return out


def gap_kind_for(item: dict) -> str:
    """What shape of help this gap needs — which decides what the "I don't know"
    button can legitimately offer.

    A design decision has real alternatives to choose between. A type question
    has candidate types. An example has to come from the student, so the most we
    can offer is the shape to fill in. A goal has nothing to offer at all.
    """
    field = required_field(item or {})
    if field in ("inputs", "outputs"):
        return "type"
    if field == "examples":
        return "example"
    if field:
        return "statement"
    return "decision"


def generate_clarifying_question(gap: dict, spec: dict, attempt: int = 1,
                                 last_answer: str | None = None) -> dict:
    """one rubric gap + current spec -> single natural-language question.

    When the same gap comes round again the student's previous answer didn't
    settle it, and saying so out loud beats silently asking a near-identical
    question and letting a counter tick up in the corner.

    Dialogue tier: this is phrasing students experience directly.
    """
    system, schema = prompts.CLARIFYING_QUESTION
    payload = json.dumps(
        {"gap": gap, "current_specification": spec,
         "attempt": attempt, "last_answer": last_answer},
        ensure_ascii=False,
    )
    raw = generate_structured(system, payload, schema, task="clarifying_question", tier=DIALOGUE)
    question = (raw.get("question") or "").strip()
    shortfall = (raw.get("shortfall") or "").strip() or None
    return {
        "question": question or f"Can you say more about: {gap.get('text', 'this')}?",
        "shortfall": shortfall if attempt > 1 else None,
    }


# --------------------------------------------------------------------------
# Persistence helpers
# --------------------------------------------------------------------------
def current_specification(db: Session, assignment_id: int, student_id: int) -> Specification | None:
    return db.scalars(
        select(Specification)
        .where(
            Specification.assignment_id == assignment_id,
            Specification.student_id == student_id,
        )
        .order_by(Specification.version.desc())
        .limit(1)
    ).first()


def start_specification(db: Session, assignment_id: int, student_id: int) -> Specification:
    existing = current_specification(db, assignment_id, student_id)
    if existing:
        return existing
    spec = Specification(
        assignment_id=assignment_id,
        student_id=student_id,
        version=1,
        content=dict(EMPTY_SPEC),
        status="draft",
        gap_ask_counts={},
        confirmed_gaps=[],
    )
    db.add(spec)
    db.commit()
    db.refresh(spec)
    return spec


def _new_version(db: Session, previous: Specification, content: dict) -> Specification:
    spec = Specification(
        assignment_id=previous.assignment_id,
        student_id=previous.student_id,
        version=previous.version + 1,
        content=content,
        status="draft",
        gap_ask_counts=dict(previous.gap_ask_counts or {}),
        confirmed_gaps=list(previous.confirmed_gaps or []),
        dont_know_counts=dict(previous.dont_know_counts or {}),
        last_question=previous.last_question,
    )
    db.add(spec)
    db.flush()
    return spec


# --------------------------------------------------------------------------
# The dialogue loop
# --------------------------------------------------------------------------
def advance(db: Session, spec: Specification, message: str) -> dict:
    """One turn: extract -> check gap -> ask, lock, or offer override.

    Returns the payload the API hands back to the client.
    """
    if spec.is_locked:
        return _result(spec, locked=True, question=None,
                       message="Specification is already locked.")

    assignment: Assignment = spec.assignment
    rubric = assignment.rubric_items

    updated = extract_fields(message, spec.content or dict(EMPTY_SPEC),
                             answering_question=spec.last_question)
    changed = _meaningful_change(spec.content or {}, updated)
    working = spec
    if changed:
        # Every clarification round produces a new version, so drift can later
        # be measured against version 1 as well as the locked one.
        working = _new_version(db, spec, updated)

    # Gaps the student has already said "my answer stands" on are settled — drop
    # them before asking, rather than re-asking and skipping one at a time.
    gap = next_gap(working.content,
                   _pending_rubric(rubric, working.confirmed_gaps, working.content))

    if gap is None:
        return _lock(db, working)

    gap_id = str(gap["id"])
    counts = dict(working.gap_ask_counts or {})
    asks = counts.get(gap_id, 0) + 1
    counts[gap_id] = asks
    working.gap_ask_counts = counts

    can_force_lock = asks >= settings.GATE_MAX_ASKS_PER_GAP
    asked = generate_clarifying_question(gap, working.content, attempt=asks,
                                         last_answer=message if asks > 1 else None)
    question = asked["question"]
    working.last_question = question

    # The same gap coming round again means the student's answer didn't settle
    # it. Say that in words rather than just ticking a counter in the rubric.
    shortfall = asked["shortfall"]
    if asks > 1 and gap.get("field"):
        # We can see precisely which half of the answer is missing; a model
        # guessing at it is strictly worse than checking the data.
        shortfall = _fallback_shortfall(gap, working.content)
    elif asks > 1 and not shortfall:
        shortfall = _fallback_shortfall(gap, working.content)

    db.commit()
    db.refresh(working)
    return _result(
        working,
        locked=False,
        question=question,
        gap=gap,
        asks=asks,
        can_force_lock=can_force_lock,
        shortfall=shortfall,
        message=(
            "You've been asked about this twice — answer again, or confirm your "
            "answer stands and move on."
            if can_force_lock else None
        ),
    )


_PLACEHOLDER = re.compile(r"\.\.\.|___|<[^>]*>|TODO", re.I)


def _fallback_shortfall(gap: dict, content: dict) -> str:
    """Said in our own words when the model doesn't supply one, so a student who
    answered and got asked again always learns why."""
    field = {"inputs": "inputs", "outputs": "outputs", "examples": "examples",
             "goal": "goal"}.get(_field_of(gap), None)
    value = (content or {}).get(field) if field else None
    if field == "examples":
        if any(_PLACEHOLDER.search(str(e)) for e in (value or [])):
            return ("That example still has placeholders in it — replace the dots "
                    "with real values so it can actually be run.")
        return "I still don't have a concrete example with real values on both sides."
    if field == "inputs":
        return "That doesn't yet say what the input is called and what type it holds."
    if field == "outputs":
        return "That doesn't yet say what comes back and what type it is."
    return "That didn't quite settle it — let's try a narrower question."


def _field_of(gap: dict) -> str | None:
    return (gap or {}).get("field")


def force_lock(db: Session, spec: Specification, gap_id: str | None = None) -> dict:
    """Student confirms their answer stands on ONE gap.

    This settles that gap; it does not lock the whole specification. The two-asks
    rule exists to stop the gate looping on a single ambiguous item — treating it
    as "unlock everything" lets a student lock a specification with no examples
    at all, which leaves the Reflection Engine nothing to check their code
    against and makes phase 3 a no-op. So: confirm the gap, then move to the next
    unmet rubric item, and only unlock when every item is either answered or
    explicitly confirmed.
    """
    if spec.is_locked:
        return _result(spec, locked=True, question=None, message="Already locked.")

    counts = spec.gap_ask_counts or {}
    if gap_id is None:
        # the gap currently on the table = most-asked, still unconfirmed
        candidates = [k for k in counts if k not in (spec.confirmed_gaps or [])]
        gap_id = max(candidates, key=lambda k: counts[k]) if candidates else None

    if gap_id is not None:
        item = next((i for i in spec.assignment.rubric_items
                     if str(i.get("id")) == str(gap_id)), None)
        field = required_field(item) if item else None
        # Same yardstick as the gate itself: a thin answer is not an answer, so
        # it can't be waived either.
        if field and not field_satisfied(field, (spec.content or {}).get(field)):
            return _result(
                spec, locked=False, question=None, can_force_lock=False,
                message=(
                    f"This one can't be waived — your specification still has no "
                    f"{field.replace('_', ' ')}, and without it there is nothing to "
                    f"check your code against at the end. Even a rough answer works."
                ),
            )
        if counts.get(str(gap_id), 0) < settings.GATE_MAX_ASKS_PER_GAP:
            return _result(
                spec, locked=False, question=None,
                message=(
                    f"That gap has only been raised {counts.get(str(gap_id), 0)} time(s). "
                    f"Override is available after {settings.GATE_MAX_ASKS_PER_GAP}."
                ),
                can_force_lock=False,
            )
        confirmed = list(spec.confirmed_gaps or [])
        if str(gap_id) not in confirmed:
            confirmed.append(str(gap_id))
        spec.confirmed_gaps = confirmed

    rubric = spec.assignment.rubric_items
    gap = next_gap(spec.content, _pending_rubric(rubric, spec.confirmed_gaps, spec.content))

    if gap is None:
        return _lock(db, spec, confirmed_any=bool(spec.confirmed_gaps))

    # Something else is still unanswered — ask about that instead of unlocking.
    next_id = str(gap["id"])
    new_counts = dict(spec.gap_ask_counts or {})
    asks = new_counts.get(next_id, 0) + 1
    new_counts[next_id] = asks
    spec.gap_ask_counts = new_counts
    question = generate_clarifying_question(gap, spec.content)["question"]
    spec.last_question = question

    db.commit()
    db.refresh(spec)
    return _result(
        spec, locked=False, question=question, gap=gap, asks=asks,
        can_force_lock=asks >= settings.GATE_MAX_ASKS_PER_GAP,
        message="Noted — that one's on record. One more thing before the editor unlocks.",
    )


def dont_know(db: Session, spec: Specification) -> dict:
    """Student pressed "I don't know" on the question currently on the table.

    This does not skip the item and does not answer it. It explains what the
    question is asking, and for a real design decision names legitimate options
    so the student can choose one. Pressing it repeatedly on the same gap is
    tracked — if a question needs explaining three times, the question is
    probably the problem, and that shows up in the instructor's data.
    """
    if spec.is_locked:
        return _result(spec, locked=True, question=None,
                       message="Specification is already locked.")

    rubric = spec.assignment.rubric_items
    gap = next_gap(spec.content, _pending_rubric(rubric, spec.confirmed_gaps, spec.content))
    if gap is None:
        return _lock(db, spec)

    gap_id = str(gap["id"])
    counts = dict(spec.dont_know_counts or {})
    counts[gap_id] = counts.get(gap_id, 0) + 1
    spec.dont_know_counts = counts

    rubric_item = next((i for i in rubric if str(i.get("id")) == gap_id), {})
    gap_kind = gap_kind_for(rubric_item)
    help_payload = explain_gap(gap, spec.content, spec.assignment.prompt_text,
                               attempt=counts[gap_id], gap_kind=gap_kind,
                               starter_code=spec.assignment.starter_code)

    # Asking for help is not the same as being asked again: it does not spend an
    # ask, but a structural item can never be waived regardless.
    asks = (spec.gap_ask_counts or {}).get(gap_id, 0)
    can_force_lock = (
        asks >= settings.GATE_MAX_ASKS_PER_GAP and not required_field(
            next((i for i in rubric if str(i.get("id")) == gap_id), {}))
    )

    question = (help_payload["question"]
                or generate_clarifying_question(gap, spec.content)["question"])
    spec.last_question = question

    db.commit()
    db.refresh(spec)
    return _result(
        spec, locked=False,
        question=question,
        gap=gap, asks=asks, can_force_lock=can_force_lock,
        explanation=help_payload["explanation"],
        options=help_payload["options"],
        message=None,
    )


def _pending_rubric(rubric: list[dict], confirmed: list | None,
                    content: dict | None = None) -> list[dict]:
    """Drop items the student has confirmed as "my answer stands".

    Structural items are never droppable: "I confirm I'm not giving an example"
    records nothing and leaves the Reflection Engine with nothing to run. The
    override exists for genuinely ambiguous judgement calls, not for skipping
    the parts that make the rest of the system work.
    """
    settled = {str(c) for c in (confirmed or [])}
    return [
        item for item in rubric
        if str(item.get("id")) not in settled
        or (required_field(item) and not (content or {}).get(required_field(item)))
    ]


def rubric_status(content: dict, rubric: list[dict], spec: Specification,
                  current_gap_id: str | None = None, locked: bool = False) -> list[dict]:
    """Per-item state for the UI, computed without an LLM call, so the student
    can see what is still outstanding instead of guessing."""
    unmet_ids = {str(i.get("id")) for i in structurally_unmet(content, rubric)}
    confirmed = {str(c) for c in (spec.confirmed_gaps or [])}
    asks = spec.gap_ask_counts or {}
    status = []
    for item in rubric:
        item_id = str(item.get("id"))
        field = required_field(item)
        if locked:
            met = True
        elif field:
            met = item_id not in unmet_ids
        else:
            # A semantic item can't be checked in code. It counts as answered
            # once the student confirmed it, or once the gate asked about it and
            # has since moved on to something else.
            met = item_id in confirmed or (
                asks.get(item_id, 0) > 0 and item_id != str(current_gap_id or "")
            )
        status.append({
            "id": item_id,
            "text": item.get("text", ""),
            "field": field,
            "met": met,
            "current": item_id == str(current_gap_id or ""),
            "confirmed": item_id in confirmed,
            "asks": asks.get(item_id, 0),
            "dont_knows": (spec.dont_know_counts or {}).get(item_id, 0),
            "checkable": bool(field),
        })
    return status


def _lock(db: Session, spec: Specification, confirmed_any: bool = False) -> dict:
    spec.status = "locked"
    db.commit()
    db.refresh(spec)
    message = ("Locked with your answers on record — the editor is unlocked."
               if confirmed_any else
               "Specification complete — the editor is unlocked.")
    content = spec.content or {}
    if not content.get("examples") and not content.get("edge_cases"):
        # Honest warning rather than a surprise at submission time.
        message += (" Heads up: you haven't given a single concrete example, so "
                    "there will be nothing to check your code against at the end.")
    return _result(spec, locked=True, question=None, message=message)


# --------------------------------------------------------------------------
def _normalise_spec(raw: dict, fallback: dict) -> dict:
    out = {}
    for field in SPEC_FIELDS:
        value = raw.get(field, fallback.get(field))
        default = EMPTY_SPEC[field]
        if isinstance(default, list):
            out[field] = value if isinstance(value, list) else ([] if value in (None, "") else [value])
        else:
            out[field] = value if isinstance(value, str) else (str(value) if value else "")
    return out


def _meaningful_change(old: dict, new: dict) -> bool:
    return any(old.get(field) != new.get(field) for field in SPEC_FIELDS)


def completeness(spec_content: dict, rubric: list[dict]) -> dict:
    """Cheap, LLM-free progress indicator for the UI — the real gate decision
    is always `next_gap`."""
    filled = sum(1 for f in SPEC_FIELDS if spec_content.get(f))
    return {"fields_filled": filled, "fields_total": len(SPEC_FIELDS),
            "rubric_total": len(rubric)}


def _result(spec: Specification, *, locked: bool, question: str | None,
            gap: dict | None = None, asks: int = 0, can_force_lock: bool = False,
            message: str | None = None, explanation: str | None = None,
            options: list[str] | None = None, shortfall: str | None = None) -> dict:
    rubric = spec.assignment.rubric_items if spec.assignment else []
    status = rubric_status(spec.content or {}, rubric, spec,
                           current_gap_id=(gap or {}).get("id"), locked=locked)
    return {
        "specification_id": spec.id,
        "version": spec.version,
        "status": spec.status,
        "locked": locked,
        "content": spec.content,
        "question": question,
        "gap": gap,
        "asks_on_gap": asks,
        "can_force_lock": can_force_lock,
        "message": message,
        "explanation": explanation,
        "shortfall": shortfall,
        "options": options or [],
        "rubric_status": status,
        "items_met": sum(1 for s in status if s["met"]),
        "items_total": len(status),
    }
