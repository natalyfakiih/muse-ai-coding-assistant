"""Chat with Ollie — the open conversation on the student's "Chat with Ollie" page.

Unlike the three-phase flow, this is free conversation, so the guard rails live
in the prompt and in what we hand the model:

* It goes through `generate_structured()` like every other call, so Mistral is
  the primary provider (Groq only if Mistral is rate-limited or down) and the
  call shows up in the ops log.
* Ollie gets the course's subjects and note titles, plus the note passages that
  match the student's latest message, so he explains from the instructor's
  material rather than from whatever the model happens to know.
* He gives hints and questions, never finished homework, and points to a
  teacher or counsellor when a student seems stuck or stressed.
* The conversation is never stored: the browser sends the recent turns with
  each message. What IS recorded is which note a question matched (or that
  none did), so the teacher sees topic trends, never what anyone wrote.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.llm.client import DIALOGUE, generate_structured
from app.llm.prompts import OLLIE_VOICE
from app.models import OllieChatTopic, Subject
from app.modules import material as material_module

router = APIRouter(tags=["ollie-chat"])

MAX_TURNS = 12  # recent turns sent to the model; older ones are dropped

# What the school layer knows about the student. Demo profile for now — the
# same "strengths first" idea as the My progress page.
DEMO_PROFILE = {
    "strengths": ["visual thinker: learns best from diagrams and everyday examples",
                  "persistent: retries questions after getting them wrong"],
    "working_on": ["consistency: practising a little every day"],
    "habits": ["focuses best in short sessions of 10 to 15 minutes"],
}

OLLIE_CHAT = OLLIE_VOICE + """

This is the open chat on the student's "Chat with Ollie" page. Your rules here:

1. Teach, never do the homework. Explain ideas, give small examples on a
   DIFFERENT problem than the one they were set, ask a guiding question, or give
   one hint at a time. Never write the finished solution, the finished code or
   the final answer to an exercise they have to hand in.
2. Build on their strengths (see the profile). They are a visual thinker, so
   reach for pictures-in-words, analogies and everyday examples.
3. Use the course notes provided. When the notes cover the question, explain
   from them and say which note it comes from. When they don't, you may still
   explain briefly, but say it isn't in their notes.
4. "Quiz me": ask ONE short question at a time on their subjects, wait for the
   answer, then say whether it's right and why.
5. "Plan my week": suggest a small, gentle plan (10–15 minutes a day) using the
   real subjects and note titles below.
6. If they seem stressed, hopeless or say something worrying, be kind, keep it
   short, and suggest talking to their teacher or the school counsellor.
7. Keep replies short: usually 2–5 sentences, a short list at most. Always end
   with one clear next step or one question.
8. Plain text. You may use **bold** and `inline code`; use a ``` block only for
   a tiny illustrative snippet that is not their exercise."""

SCHEMA = '{"reply": "string — what Ollie says next"}'


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    messages: list[ChatTurn] = Field(min_length=1, max_length=60)
    student_name: str | None = Field(default=None, max_length=80)
    student_id: int | None = None
    course_id: str | None = Field(default=None, max_length=80)


def _course_context(db: Session, course_id: str | None, question: str) -> dict:
    query = select(Subject).order_by(Subject.id)
    if course_id:
        query = query.where(Subject.course_id == course_id)
    subjects = db.scalars(query).all()

    excerpts: list[dict] = []
    for subject in subjects:
        for chunk in material_module.find_relevant(db, subject.id, question, limit=2):
            excerpts.append({"subject": subject.name, "note": chunk["title"],
                             "text": chunk["text"], "score": chunk["score"],
                             "subject_id": subject.id, "material_id": chunk["material_id"]})
    excerpts.sort(key=lambda c: c["score"], reverse=True)

    return {
        "subjects": [
            {"name": s.name, "description": s.description,
             "notes": [m.title for m in sorted(s.materials, key=lambda m: m.position)]}
            for s in subjects
        ],
        "matching_note_passages": [
            {k: v for k, v in c.items() if k in ("subject", "note", "text")} for c in excerpts[:3]
        ],
        "_top": excerpts[0] if excerpts else None,
    }


def _offline_reply(name: str, last: str) -> str:
    """Used only when the app runs on the offline stub (no API keys)."""
    text = last.lower()
    if "quiz" in text:
        return ("Let's try one. In Python, what does a function hand back if it "
                "never reaches a `return` line? Have a guess, then tell me why.")
    if "plan" in text or "week" in text:
        return ("Here's a gentle idea: 10 minutes a day, one note at a time, and a "
                "short practice on Friday. Which subject would you like to start with?")
    return (f"I'm running in offline demo mode right now, {name}, so I can't think "
            "properly yet. Add a MISTRAL_API_KEY to .env and restart the server, "
            "and I'll be able to really help.")


@router.post("/ollie/chat")
def ollie_chat(body: ChatRequest, db: Session = Depends(get_db)):
    turns = body.messages[-MAX_TURNS:]
    last_user = next((t.content for t in reversed(turns) if t.role == "user"), "")
    name = (body.student_name or "there").strip() or "there"

    course = _course_context(db, body.course_id, last_user)
    top = course.pop("_top")

    # The topic, never the text: which note this question matched, if any.
    db.add(OllieChatTopic(
        course_id=body.course_id, student_id=body.student_id,
        subject_id=top["subject_id"] if top else None,
        material_id=top["material_id"] if top else None,
        note_title=top["note"] if top else None,
        covered=bool(top),
    ))
    db.commit()

    payload = {
        "student_name": name,
        "student_profile": DEMO_PROFILE,
        "course": course,
        "conversation": [{"role": t.role, "content": t.content} for t in turns],
        "instruction": "Write Ollie's next reply to the last user message.",
    }

    result = generate_structured(
        OLLIE_CHAT,
        json.dumps(payload, ensure_ascii=False),
        SCHEMA,
        task="ollie_chat",
        tier=DIALOGUE,
        temperature=0.6,
    )

    reply = result.get("reply") if isinstance(result, dict) else None
    live = settings.llm_enabled
    if not isinstance(reply, str) or not reply.strip():
        reply = _offline_reply(name, last_user) if not live else (
            "Sorry, I lost my train of thought there. Could you ask me that again?")
    return {"reply": reply.strip(), "mode": "live" if live else "offline-stub"}


@router.get("/courses/{course_id}/analytics/ollie-topics")
def ollie_topics(course_id: str, days: int = 7, subject_id: int | None = None,
                 db: Session = Depends(get_db)):
    """For the teacher: what students asked Ollie about, by note. Counts only:
    how many questions and how many different students, never the questions."""
    since = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 365)))
    rows = db.scalars(
        select(OllieChatTopic)
        .where(OllieChatTopic.course_id == course_id)
        .where(OllieChatTopic.timestamp >= since)
    ).all()
    if subject_id is not None:
        # Questions that matched no note have no subject; with a subject picked
        # they are left out, since nothing says which subject they were about.
        rows = [r for r in rows if r.subject_id == subject_id]
    names = {s.id: s.name for s in db.scalars(select(Subject)).all()}

    topics: dict = defaultdict(lambda: {"questions": 0, "students": set()})
    uncovered = {"questions": 0, "students": set()}
    for r in rows:
        bucket = topics[(r.subject_id, r.note_title)] if r.covered else uncovered
        bucket["questions"] += 1
        if r.student_id is not None:
            bucket["students"].add(r.student_id)

    out = sorted(
        ({"subject": names.get(sid, "—"), "note": note, "questions": v["questions"],
          "students": len(v["students"])} for (sid, note), v in topics.items()),
        key=lambda t: (-t["questions"], t["note"] or ""))
    return {
        "days": days,
        "total_questions": len(rows),
        "students": len({r.student_id for r in rows if r.student_id is not None}),
        "topics": out,
        "not_in_notes": {"questions": uncovered["questions"], "students": len(uncovered["students"])},
    }
