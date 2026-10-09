"""Ollie explaining from the instructor's material, and only from it.

The rule the instructor chose is strict: Ollie answers out of the course notes
or he says it is not covered. That is worth more than it sounds. An instructor
can trust what their students are being told, nothing contradicts how the
course teaches a topic, and a student revising the night before an exam is not
quietly taught a different method than the one they will be marked on.

Keeping that promise means it cannot live in the prompt. "Only use the provided
material" is a request, and a model that finds the excerpts thin will help
anyway — politely, fluently and from its own knowledge. So the refusal is in
Python: if nothing in the material scores above the floor, no model call
happens at all. There is no prompt to ignore, because there is no prompt.

Retrieval is plain word overlap rather than embeddings. It is deterministic,
needs no API key, runs offline with the rest of the test suite, and is honest
about what it is: for a few pages of notes it finds the right paragraph, and
when it doesn't, the floor catches it and Ollie says so.
"""
from __future__ import annotations

import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm import DIALOGUE, generate_structured
from app.llm import prompts
from app.models import Subject, SubjectMaterial

# Below this, the material does not really cover the question and Ollie says so
# instead of guessing. Tuned so a question sharing two or three real words with
# a paragraph gets through, and a question sharing only "the" and "is" does not.
RELEVANCE_FLOOR = 0.18
MAX_EXCERPTS = 3

# --- the Learn page's floors ---------------------------------------------
# A taught point has to trace back to the note. Scored the other way round from
# retrieval: the share of the POINT's own content words that appear in the note.
# A paraphrase of a paragraph shares most of its nouns; a point the model
# brought in from its own knowledge shares almost none, and gets dropped.
#
# 0.45 rather than something stricter because teaching is allowed to reword:
# "velocity changes by the same amount each second" from a note that says
# "constant acceleration means equal changes in velocity per unit time" is a
# good point and scores about 0.5. Set it at 0.7 and honest teaching gets cut.
GROUNDING_FLOOR = 0.45
# Under this there is nothing to teach from, so no model call is made at all.
MIN_TEACHABLE_WORDS = 40
MAX_POINTS = 4

_STOPWORDS = frozenset("""
a an and are as at be but by can do does for from how i if in is it its my not of on or so that
the this to was what when where which who why with you your me am get got need help please
explain tell about there their them they then than he she we us our will would should could
""".split())


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", str(text or "").lower())
            if len(w) > 2 and w not in _STOPWORDS}


def chunks_for(db: Session, subject_id: int) -> list[dict]:
    """Every material row split into paragraphs.

    Paragraph-sized rather than whole-document: a page of notes about three
    topics would otherwise score as relevant to all three, and Ollie would be
    handed two topics of noise around the one that mattered.
    """
    rows = db.scalars(
        select(SubjectMaterial)
        .where(SubjectMaterial.subject_id == subject_id)
        .order_by(SubjectMaterial.position, SubjectMaterial.id)
    ).all()

    out: list[dict] = []
    for row in rows:
        for index, part in enumerate(re.split(r"\n\s*\n", row.body or "")):
            text = part.strip()
            if len(text) < 25:          # headings and stray lines
                continue
            out.append({"material_id": row.id, "title": row.title,
                        "index": index, "text": text})
    return out


def find_relevant(db: Session, subject_id: int, question: str,
                  limit: int = MAX_EXCERPTS) -> list[dict]:
    """The passages that actually bear on the question, best first."""
    asked = _words(question)
    if not asked:
        return []

    scored = []
    for chunk in chunks_for(db, subject_id):
        have = _words(chunk["text"] + " " + (chunk["title"] or ""))
        if not have:
            continue
        overlap = len(asked & have)
        if not overlap:
            continue
        # Share of the QUESTION that the passage covers. Scoring by share of the
        # passage would make a long paragraph look irrelevant just for being
        # long, and a one-line passage look authoritative for sharing one word.
        score = overlap / len(asked)
        scored.append({**chunk, "score": round(score, 3)})

    scored.sort(key=lambda c: c["score"], reverse=True)
    return [c for c in scored if c["score"] >= RELEVANCE_FLOOR][:limit]


def explain(db: Session, subject_id: int, question: str) -> dict:
    """Ollie's answer, or an honest refusal. No model call happens unless the
    material has something to say."""
    subject = db.get(Subject, subject_id)
    if subject is None:
        raise LookupError("No such subject.")

    excerpts = find_relevant(db, subject_id, question)
    if not excerpts:
        # The guarantee, enforced here rather than requested in a prompt.
        return {
            "subject_id": subject_id,
            "covered": False,
            "answer": (
                f"I can't find that in the {subject.name} notes, so I'd be "
                f"guessing. Ask your instructor, or point me at the part of the "
                f"material it should be in."
            ),
            "excerpts": [],
        }

    system, schema = prompts.EXPLAIN_MATERIAL
    payload = json.dumps({
        "subject": subject.name,
        "question": question,
        "material": [{"title": c["title"], "text": c["text"]} for c in excerpts],
    }, ensure_ascii=False)
    raw = generate_structured(system, payload, schema,
                              task="explain_material", tier=DIALOGUE)

    answer = (raw.get("answer") or "").strip()
    return {
        "subject_id": subject_id,
        # The model gets to say the excerpts did not actually answer it, which
        # is a different thing from the material not mentioning the topic.
        "covered": bool(raw.get("covered", True)) and bool(answer),
        "answer": answer or (
            f"The {subject.name} notes touch on that but don't really answer it. "
            f"Worth asking your instructor."),
        "excerpts": [{"title": c["title"], "text": c["text"][:400],
                      "score": c["score"]} for c in excerpts],
    }


# --- teaching one note, the Learn page -----------------------------------
"""Why a lesson and not just the paragraph.

Reading a page of notes feels like learning and mostly isn't: a student who has
just read something fluently cannot tell the difference between recognising it
and being able to produce it. The two things that reliably move material into
long-term memory are elaboration — tying the new idea to something already
concrete — and retrieval practice, being asked to produce the answer before
seeing it. So the lesson Ollie builds is deliberately shaped: one idea, a few
steps, an analogy for the mechanism, and a question the student answers with
the note hidden.

The note itself stays one click away. This replaces nothing; it is a way in.
"""


def _squeeze(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").lower())


def _grounded(text: str, note: set[str], note_blob: str = "") -> float:
    """How much of `text` is actually in the note. 1.0 means every content word
    of it appears there; 0.0 means none of it does.

    There is a hole in word overlap, and in a maths course it is the important
    one: `v = u + a t` has no content words at all — `_words` drops anything
    under three characters — so scoring it by overlap gives 0 and the floor
    throws away the line the note is actually about.

    So anything with too few scoreable words to judge falls back to a verbatim
    check: squeeze the whitespace out and ask whether the note contains that
    sequence. It cannot let an invention through, because an equation the note
    does not contain is not in the note's text either. It only rescues a line
    that was quoted exactly, which is the one case overlap cannot see.
    """
    words = _words(text)
    if len(words) >= 3:
        return len(words & note) / len(words)

    squeezed = _squeeze(text)
    if len(squeezed) >= 4 and note_blob and squeezed in note_blob:
        return 1.0
    return (len(words & note) / len(words)) if words else 0.0


def _traced_to(text: str, paragraphs: list[str]) -> int | None:
    """Which paragraph of the note a taught point came from, so the student can
    go and read it. Highest word overlap wins; nothing with no overlap at all."""
    words = _words(text)
    best, score = None, 0
    for index, para in enumerate(paragraphs):
        overlap = len(words & _words(para))
        if overlap > score:
            best, score = index, overlap
    return best


def teach(db: Session, subject_id: int, material_id: int) -> dict:
    """A lesson on one note: hook, idea, steps, worked example, memory hook and
    a question to answer before the answer is shown.

    Two guarantees enforced here rather than requested in the prompt:

    1. A note with nothing in it gets no lesson. No model call happens, so
       there is no chance of a fluent lesson about a heading.
    2. Every taught point is checked back against the note's own words, and a
       point that does not trace to it is dropped before the student sees it.
       The hook and the memory hook are exempt and labelled as such in the UI —
       an analogy is supposed to be new wording, that is what makes it stick.
       The check question's answer is not exempt: a student must never be
       quizzed on something their notes do not answer.
    """
    subject = db.get(Subject, subject_id)
    if subject is None:
        raise LookupError("No such subject.")

    row = db.get(SubjectMaterial, material_id)
    if row is None or row.subject_id != subject_id:
        raise LookupError("No such material in this subject.")

    body = (row.body or "").strip()
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    base = {"subject_id": subject_id, "material_id": row.id, "title": row.title}

    if len(body.split()) < MIN_TEACHABLE_WORDS:
        # Guarantee 1, in Python: nothing to teach, so nothing is invented.
        return {
            **base, "taught": False,
            "idea": (f"There isn't enough written down in \"{row.title}\" for me to "
                     f"teach from yet. Have a read of what's there, and ask your "
                     f"instructor to add to it."),
            "hook": "", "points": [], "example": None, "memory_hook": "",
            "check": None, "dropped": 0,
        }

    system, schema = prompts.TEACH_MATERIAL
    payload = json.dumps({
        "subject": subject.name,
        "note_title": row.title,
        "note": body,
    }, ensure_ascii=False)
    raw = generate_structured(system, payload, schema,
                              task="teach_material", tier=DIALOGUE)

    note_words = _words(body)
    note_blob = _squeeze(body)

    # Guarantee 2: every point is scored against the note and ungrounded ones
    # never reach the student. `dropped` is reported rather than hidden — an
    # instructor seeing points disappear from their own note is seeing the
    # model drift, which is worth knowing.
    points, dropped = [], 0
    for point in (raw.get("points") or [])[:MAX_POINTS + 2]:
        if not isinstance(point, dict):
            continue
        title = str(point.get("title") or "").strip()
        text = str(point.get("body") or "").strip()
        if not text:
            continue
        # Scored separately rather than as one string. Concatenating them
        # defeats the verbatim fallback: a point whose title and body are both
        # the same quoted equation becomes "v = u + a t v = u + a t", which the
        # note does not contain, and the one case the fallback exists for is
        # the one it then fails.
        #
        # The body carries the claim, so it decides. A title is three or four
        # words and cannot smuggle a fact past a grounded body — but a title
        # with no connection to the note at all, while the body is fine, means
        # the two do not belong together, and that goes too.
        score = _grounded(text, note_words, note_blob)
        label_adrift = (len(_words(title)) >= 3
                        and _grounded(title, note_words, note_blob) == 0.0)
        if score < GROUNDING_FLOOR or label_adrift:
            dropped += 1
            continue
        points.append({"title": title, "body": text,
                       "grounding": round(score, 2),
                       "paragraph": _traced_to(text, paragraphs)})
        if len(points) >= MAX_POINTS:
            break

    example = str(raw.get("example") or "").strip() or None
    if example and _grounded(example, note_words, note_blob) < GROUNDING_FLOOR:
        example, dropped = None, dropped + 1

    check = raw.get("check") if isinstance(raw.get("check"), dict) else {}
    question = str((check or {}).get("question") or "").strip()
    answer = str((check or {}).get("answer") or "").strip()
    # An unanswerable question is worse than no question: the student retrieves,
    # fails, and learns that their own notes are unreliable.
    if not (question and answer) or \
            _grounded(answer, note_words, note_blob) < GROUNDING_FLOOR:
        check = None
    else:
        check = {"question": question, "answer": answer}

    idea = str(raw.get("idea") or "").strip()
    return {
        **base,
        # A lesson with no grounded points is not a lesson, however fluent the
        # prose around it was.
        "taught": bool(points and idea),
        "hook": str(raw.get("hook") or "").strip(),
        "idea": idea or f"Read \"{row.title}\" below — I couldn't summarise it cleanly.",
        "points": points,
        "example": example,
        "memory_hook": str(raw.get("memory_hook") or "").strip(),
        "check": check,
        "dropped": dropped,
        "paragraphs": paragraphs,
    }
