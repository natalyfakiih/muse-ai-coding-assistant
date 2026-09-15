"""Deterministic offline stub.

Active when no API keys are set, or with USE_FAKE_LLM=1. It exists so the whole
three-phase flow — gate, tutor, reflection, dashboard — is runnable and testable
without spending quota or waiting on a network. The heuristics are crude by
design; this is a test double, not a second implementation.
"""
from __future__ import annotations

import json
import re
from typing import Any

_CODE_HINT = re.compile(
    r"\b(my|this)\s+(code|loop|function|program|script)\b|Traceback|Error:|line \d+",
    re.IGNORECASE,
)
_IDENTIFIER_HINT = re.compile(r"\b(def|return|for|while|if)\b|\w+\(\)|\w+\[\w*\]")

_EDGE_KEYWORDS = {
    "empty": "empty input",
    "zero": "zero / n = 0",
    "negative": "negative input",
    "none": "None / missing value",
    "duplicate": "duplicate values",
    "large": "large input",
}


_ELEMENT_TYPES = {
    "integer": "int", "integers": "int", "int": "int", "ints": "int",
    "string": "str", "strings": "str", "str": "str", "strs": "str",
    "float": "float", "floats": "float", "decimal": "float", "decimals": "float",
    "bool": "bool", "boolean": "bool", "booleans": "bool",
}
_CONTAINERS = {"list": "list", "lists": "list", "array": "list", "arrays": "list",
               "dict": "dict", "set": "set", "tuple": "tuple"}
_STOPWORDS_NAME = {"a", "an", "the", "of", "in", "and", "or", "it", "its", "list",
                   "integers", "integer", "strings", "string", "numbers", "number",
                   "elements", "values", "items_", "type", "input", "output"}


def _named_in(segment: str) -> str | None:
    """'...called items' -> 'items'. The explicit naming phrase wins."""
    match = re.search(r"\b(?:called|named)\s+([a-z_]\w*)", segment)
    return match.group(1) if match else None


def _trailing_name(segment: str) -> str | None:
    """'takes an int n' -> 'n'. Last resort, and it must not be a filler word —
    'takes a list of integers' would otherwise name the input 'of'."""
    words = re.findall(r"[a-z_]\w*", segment)
    for word in reversed(words):
        if word not in _STOPWORDS_NAME and word not in _ELEMENT_TYPES \
                and word not in _CONTAINERS:
            return word
    return None


def _type_in(segment: str) -> str:
    """'a list of integers' -> 'list[int]'; 'an int' -> 'int'."""
    container = next((v for k, v in _CONTAINERS.items()
                      if re.search(rf"\b{k}\b", segment)), None)
    element = next((v for k, v in _ELEMENT_TYPES.items()
                    if re.search(rf"\b{k}\b", segment)), None)
    if container and element:
        return f"{container}[{element}]"
    return container or element or ""


def _payload(user_payload: str) -> dict[str, Any]:
    try:
        data = json.loads(user_payload)
        return data if isinstance(data, dict) else {"text": user_payload}
    except (json.JSONDecodeError, TypeError):
        return {"text": user_payload}


def fake_generate(task: str, system_prompt: str, user_payload: str) -> dict:
    data = _payload(user_payload)
    handler = _HANDLERS.get(task, _unknown)
    return handler(data)


# --------------------------------------------------------------------------
def _extract_fields(data: dict) -> dict:
    spec = dict(data.get("current_specification") or {})
    message = (data.get("student_message") or "").strip()

    spec.setdefault("goal", "")
    for key in ("inputs", "outputs", "edge_cases", "examples", "explicit_exclusions"):
        spec.setdefault(key, [])

    if not spec["goal"] and message:
        spec["goal"] = message.split(".")[0][:200]

    # A short reply answers the question that was just asked, so fold the
    # question's wording in before matching — otherwise "an empty list" is
    # recorded with no trace of the condition it applies to.
    asked = (data.get("answering_question") or "").strip()
    if asked and len(message.split()) <= 12:
        message = f"{asked} {message}"

    low = message.lower()
    for keyword, label in _EDGE_KEYWORDS.items():
        if keyword not in low:
            continue
        # Keep the sentence the student actually wrote, not just the label —
        # "for n = 0 it should return an empty list" carries the expectation
        # that the Reflection Engine later checks against.
        sentence = next(
            (s.strip() for s in re.split(r"(?<=[.!?])\s+|\n", message) if keyword in s.lower()),
            label,
        )
        entry = sentence if len(sentence) <= 160 else label
        if not any(keyword in existing.lower() for existing in spec["edge_cases"]):
            spec["edge_cases"].append(entry)

    # "takes a list of integers called items" / "takes an int n" / "returns a list of ints"
    taken = re.search(r"\b(?:takes?|accepts?|receives?)\b(.*)", low)
    if taken:
        # Stop at the return clause, or "takes an int n and returns a list of
        # ints" reads the input's type off the output's half of the sentence.
        segment = re.split(r"\b(?:and\s+)?returns?\b|->|=>", taken.group(1))[0]
        name = _named_in(segment) or _trailing_name(segment)
        type_name = _type_in(segment)
        if name and not any(i.get("name") == name for i in spec["inputs"]):
            spec["inputs"].append({"name": name, "type": type_name, "constraints": ""})

    returned = re.search(r"\breturns?\b(.*)", low)
    if returned:
        type_name = _type_in(returned.group(1))
        existing = (spec["outputs"][0].get("type") if spec["outputs"] else "")
        # "list[int]" beats a bare "list" recorded from an earlier, vaguer line.
        if type_name and (not spec["outputs"] or len(type_name) > len(existing or "")):
            spec["outputs"] = [{"name": "result", "type": type_name}]

    # "n=3 -> [0, 1, 1]" — an explicit arrow, with an argument-shaped left side.
    # Deliberately strict: a loose pattern here turns half a sentence into a
    # test case, and the Reflection Engine then runs it.
    for match in re.finditer(
        r"(\b[\w\s]*?[\w\]\)](?:\s*=\s*|\()[^->\n]{0,40}?)\s*(?:->|=>)\s*(\[[^\]]*\]|\([^)]*\)|-?[\w.]+)",
        message,
    ):
        left = match.group(1).strip().rstrip(",")
        if not re.search(r"[=(]", left) or len(left) > 60:
            continue
        # "for example n=3" -> "n=3": the lead-in words are prose, and passing
        # them to the sandbox as arguments turns a clean case into a TypeError.
        start = re.search(r"\w+\s*[=(]", left)
        if start:
            left = left[start.start():]
        example = {"input": left, "output": match.group(2).strip()}
        if example not in spec["examples"]:
            spec["examples"].append(example)

    spec["changed"] = True
    return spec


def _next_gap(data: dict) -> dict:
    spec = data.get("current_specification") or {}
    rubric = data.get("rubric") or []
    unmet = []
    for item in rubric:
        text = (item.get("text") if isinstance(item, dict) else str(item)) or ""
        item_id = item.get("id") if isinstance(item, dict) else text[:40]
        if not _rubric_item_met(item_id, text, spec):
            unmet.append({"id": item_id, "text": text})
    if not unmet:
        return {"gap": None, "unmet_items": []}
    return {"gap": unmet[0], "unmet_items": [u["text"] for u in unmet]}


def _rubric_item_met(item_id: str, text: str, spec: dict) -> bool:
    low = f"{item_id} {text}".lower()
    blob = json.dumps(spec).lower()
    # Specific markers first: "empty_input" contains "input", and checking the
    # generic branch first would mark the empty-input rubric item answered the
    # moment any input was named.
    if "empty" in low or "n = 0" in low or "n=0" in low:
        return "empty" in blob or "zero" in blob or "n = 0" in blob or "n=0" in blob
    if "negative" in low:
        return "negative" in blob
    if "order" in low:
        return "order" in blob
    if "example" in low:
        return bool(spec.get("examples"))
    if "edge" in low:
        return bool(spec.get("edge_cases"))
    if "goal" in low or "what the function does" in low or "what the program does" in low:
        return bool((spec.get("goal") or "").strip())
    if "input" in low:
        return bool(spec.get("inputs"))
    if "output" in low or "return" in low:
        return bool(spec.get("outputs"))
    keywords = [w for w in re.findall(r"[a-z]{4,}", low) if w not in {"must", "state", "behavior", "behaviour", "specify", "should"}]
    return any(word in blob for word in keywords) if keywords else True


def _explain_gap(data: dict) -> dict:
    gap = data.get("gap") or {}
    text = gap.get("text") if isinstance(gap, dict) else str(gap)
    low = str(text).lower()
    attempt = int(data.get("attempt") or 1)

    kind = data.get("gap_kind") or "decision"
    background = str(data.get("assignment_prompt_background_only") or "").lower()
    list_shaped = "list" in background

    if kind == "type":
        # Candidate types are a real choice, so offering them is fair. The name
        # comes from the starter code's signature — an option that answers only
        # half the question gets rejected the moment the student clicks it.
        params = data.get("parameter_names") or []
        name = params[0] if params else ("items" if list_shaped else "n")
        if list_shaped:
            options = [("a list of integers", f"It takes a list of integers called {name}."),
                       ("a list of strings", f"It takes a list of strings called {name}.")]
        else:
            options = [("a whole number", f"It takes an integer called {name}."),
                       ("a decimal number", f"It takes a float called {name}.")]
        return {
            "explanation": (f"[offline stub] This is asking what kind of data is "
                            f"involved: {text}. Pick the one your function is for — "
                            "the type is what lets your code be checked later."),
            "options": [{"label": label, "answer": answer} for label, answer in options],
            "question": "Which of those is it?",
        }

    if kind == "example":
        # Never a finished example — that would be the answer. Just the shape.
        shape = ("items=[...] -> [...]" if list_shaped else "n=... -> ...")
        return {
            "explanation": (f"[offline stub] This one has to come from you: {text}. "
                            "Give one input you could actually call the function "
                            "with, and exactly what should come back. Use the shape "
                            "below and replace the dots with real values."),
            "options": [{"label": "fill in this shape", "answer": shape}],
            "question": "What real values would you use?",
        }

    if kind == "statement":
        return {
            "explanation": (f"[offline stub] This is asking you to state something "
                            f"in your own words: {text}. One plain sentence is enough."),
            "options": [],
            "question": "How would you say it?",
        }

    # Each option carries a full sentence, so what gets recorded still names the
    # condition it answers.
    if "empty" in low or "n = 0" in low or "n=0" in low:
        condition, options = "when n is 0", [
            ("return an empty list", "When n is 0, return an empty list."),
            ("raise an error", "When n is 0, raise a ValueError."),
        ]
    elif "negative" in low:
        condition, options = "when n is negative", [
            ("treat it like zero", "When n is negative, return an empty list."),
            ("raise an error", "When n is negative, raise a ValueError."),
        ]
    elif "order" in low:
        condition, options = "about ordering", [
            ("keep the original order", "The output keeps the original order of the input."),
            ("order doesn't matter", "The order of the output does not matter."),
        ]
    else:
        condition, options = "", []

    if attempt <= 1:
        explanation = (f"[offline stub] This is asking you to decide what happens "
                       f"{condition or 'in this case'}. There's no single right answer — "
                       "you pick one and write it down, so your code can be checked "
                       "against it later.")
    else:
        # Repeating the same words is what makes it feel stuck; go concrete.
        example = options[0][1] if options else "a specific, checkable sentence"
        explanation = (f"[offline stub] Another way to see it: someone calls your "
                       f"function {condition or 'in that situation'} — what comes back? "
                       f"Whatever you answer becomes a test, so it has to be concrete. "
                       f'For instance: "{example}"')

    return {
        "explanation": explanation,
        "options": [{"label": label, "answer": answer} for label, answer in options],
        "question": ("Which of those do you want?" if attempt <= 1
                     else "Pick one, or type your own sentence."),
    }


def _clarifying_question(data: dict) -> dict:
    gap = data.get("gap") or {}
    text = gap.get("text") if isinstance(gap, dict) else str(gap)
    attempt = int(data.get("attempt") or 1)
    if attempt > 1:
        return {
            "question": f"Let's narrow it down — {text}? One sentence is enough.",
            "shortfall": "[offline stub] That answer didn't settle this one yet.",
        }
    return {"question": f"Before you start coding — {text}? Say it in your own words.",
            "shortfall": None}


def _classify_question(data: dict) -> dict:
    message = data.get("student_message") or ""
    if _CODE_HINT.search(message):
        return {"type": "problem_solving", "confidence": 0.85, "reasoning": "refers to own code"}
    if re.search(r"\b(what does|what is|how do i|syntax|difference between)\b", message, re.I) \
            and not re.search(r"\bmine\b|\bmy\b", message, re.I):
        return {"type": "factual", "confidence": 0.7, "reasoning": "definitional"}
    if _IDENTIFIER_HINT.search(message):
        return {"type": "problem_solving", "confidence": 0.7, "reasoning": "names code constructs"}
    return {"type": "problem_solving", "confidence": 0.5, "reasoning": "default"}


def _factual_answer(data: dict) -> dict:
    message = (data.get("student_message") or "").strip()
    return {
        "answer": f"[offline stub] A short reference answer to: {message}",
        "deflect": False,
    }


def _socratic_question(data: dict) -> dict:
    prior = data.get("prior_counter_questions_this_thread") or []
    ladder = [
        "What value do you expect that variable to hold the first time through the loop?",
        "Run it with the smallest input you declared — what is the actual value at that line?",
        "Which line is the first one where the actual value stops matching what you expected?",
    ]
    question = ladder[min(len(prior), len(ladder) - 1)]
    return {"question": question, "targets": "loop variable on the first iteration"}


def _direct_hint(data: dict) -> dict:
    return {
        "hint": "[offline stub] The mismatch is in how the loop boundary is set. "
                "Re-check the range you iterate over, then verify the output for "
                "the smallest declared example."
    }


def _materialize_edge_cases(data: dict) -> dict:
    cases = []
    inputs = data.get("inputs") or []
    first = inputs[0].get("name") if inputs and isinstance(inputs[0], dict) else "n"
    input_is_list = "list" in json.dumps(inputs).lower()
    for edge in data.get("edge_cases") or []:
        low = str(edge).lower()
        if "negative" in low:
            value = "-1"
        elif "empty" in low:
            value = "[]" if input_is_list else "0"
        elif "zero" in low or "= 0" in low or "n=0" in low:
            value = "0"
        else:
            value = "[]" if input_is_list else "1"

        # Pull a declared expectation out of the student's own wording:
        # "... should return an empty list" / "... returns []"
        expected = None
        match = re.search(r"(?:should\s+)?returns?\s+(?:an?\s+|the\s+)?(\[[^\]]*\]|empty list|none|-?\d+)", low)
        if match:
            word = match.group(1)
            expected = "[]" if word in ("empty list",) else ("None" if word == "none" else word)

        cases.append({
            "edge_case": str(edge),
            "input": f"{first}={value}",
            "expected": expected,
            "declared": expected is not None,
        })
    return {"cases": cases}


def _diagnostic_question(data: dict) -> dict:
    prior = data.get("prior_questions") or []
    if data.get("category") == "revision":
        return {"question": "[offline stub] What made you change your mind about this?",
                "targets": "the decision, not the code"}
    ladder = [
        "[offline stub] For this input, what did you expect the value to be at the "
        "moment the function returns?",
        "[offline stub] How many times does the loop body actually run for that input?",
        "[offline stub] Which is the first input where your result stops matching "
        "what you wrote down?",
    ]
    return {"question": ladder[min(len(prior), len(ladder) - 1)],
            "targets": "the returned value for the failing input"}


def _classify_diff(data: dict) -> dict:
    log = json.dumps(data.get("interaction_log") or []).lower()
    item = str(data.get("specification_item") or "")
    generic = {"example", "edge", "case", "input", "output", "expected", "declared",
               "returns", "return", "function", "value", "list", "none", "null"}
    tokens = [w for w in re.findall(r"[a-z]{4,}", item.lower()) if w not in generic][:4]
    discussed = any(token in log for token in tokens) if tokens else False
    result = data.get("test_result") or {}
    if discussed:
        category = "revision"
    elif result.get("error"):
        category = "bug"
    else:
        category = "drift"
    return {
        "category": category,
        "evidence": "[offline stub] matched keywords in interaction log" if discussed else None,
        "description": f"Declared behaviour for {item!r} does not match what the code does.",
    }


def _narrative_summary(data: dict) -> dict:
    spec_versions = data.get("specification_versions") or 0
    counts = data.get("interaction_counts") or {}
    diffs = data.get("diff_categories") or {}
    return {
        "summary": (
            f"[offline stub] Wrote the specification {spec_versions} time(s). "
            f"Asked {counts.get('problem_solving', 0)} problem-solving and "
            f"{counts.get('factual', 0)} factual questions. "
            f"Reflection produced {sum(diffs.values())} discrepancy/ies "
            f"({', '.join(f'{k}: {v}' for k, v in diffs.items()) or 'none'})."
        )
    }


def _unknown(data: dict) -> dict:
    return {"error": "no offline stub for this task", "echo": data}


_HANDLERS = {
    "extract_fields": _extract_fields,
    "next_gap": _next_gap,
    "clarifying_question": _clarifying_question,
    "explain_gap": _explain_gap,
    "classify_question": _classify_question,
    "factual_answer": _factual_answer,
    "socratic_question": _socratic_question,
    "direct_hint": _direct_hint,
    "materialize_edge_cases": _materialize_edge_cases,
    "classify_diff": _classify_diff,
    "diagnostic_question": _diagnostic_question,
    "narrative_summary": _narrative_summary,
}
