"""The Learn page: Ollie teaching a note instead of answering a question.

What is being protected here is the same promise the explain panel makes, moved
to a harder case. When a student asks a question, the material either mentions
the topic or it doesn't, and `find_relevant` settles it before any model runs.
A lesson has no question to score against — the model is handed a note and asked
to teach it, which is exactly the situation where a helpful model fills in the
bits the note left out. A student revising from that lesson would be learning
things their course never taught and will not be marked on.

So the grounding check runs the other way round: every taught point is scored
against the note's own words after the model returns, and anything that doesn't
trace back is dropped before the student sees it. These tests feed the engine a
lesson with one invented point in it and check that the invented one is the one
that disappears.
"""
from __future__ import annotations

import pytest

from app.models import Subject, SubjectMaterial
from app.modules import material as material_module


NOTE = """Constant acceleration means the velocity changes by the same amount
in every second of the motion.

If a body starts at rest and accelerates at 2 m/s squared, after 3 seconds its
velocity is 6 m/s. The velocity is the acceleration multiplied by the time.

The units matter. An acceleration in metres per second squared multiplied by a
time in seconds leaves metres per second, which is a velocity.
"""


@pytest.fixture()
def subject(db):
    row = Subject(course_id="teach-test", name="Mechanics", checker="math")
    db.add(row)
    db.commit()
    note = SubjectMaterial(subject_id=row.id, title="Constant acceleration",
                           body=NOTE, position=0)
    db.add(note)
    db.commit()
    return row, note


# --------------------------------------------------------------------------
# the guarantee
# --------------------------------------------------------------------------
def test_an_invented_point_is_dropped_and_a_grounded_one_is_kept(db, subject, monkeypatch):
    """The whole reason this module exists.

    Both points below are fluent and plausible. One paraphrases the note; the
    other teaches the SUVAT equation, which this note never mentions. A student
    who met it here would take it into an exam as something their course taught
    them.
    """
    row, note = subject
    lesson = {
        "hook": "Why does a dropped coin keep speeding up?",
        "idea": "Constant acceleration means velocity changes by the same amount each second.",
        "points": [
            {"title": "Equal changes each second",
             "body": "With a constant acceleration the velocity changes by the "
                     "same amount in every second of the motion."},
            {"title": "Use the SUVAT equation",
             "body": "Apply s = ut + half a t squared to find the displacement, "
                     "and differentiate it twice to recover the jerk."},
        ],
        "example": "A body at rest accelerating at 2 m/s squared reaches 6 m/s after 3 seconds.",
        "memory_hook": "Think of a metronome adding one notch of speed per tick.",
        "check": {"question": "What is the velocity after 3 seconds at 2 m/s squared?",
                  "answer": "6 m/s, because velocity is the acceleration multiplied by the time."},
    }
    monkeypatch.setattr(material_module, "generate_structured",
                        lambda *a, **k: lesson)

    out = material_module.teach(db, row.id, note.id)

    titles = [p["title"] for p in out["points"]]
    assert "Equal changes each second" in titles
    assert "Use the SUVAT equation" not in titles, \
        "a point the note never makes was taught to the student"
    assert out["dropped"] == 1
    assert out["taught"] is True


def test_every_kept_point_says_which_paragraph_it_came_from(db, subject, monkeypatch):
    """A lesson the student cannot check against the source is just a lesson
    they have to take on trust."""
    row, note = subject
    monkeypatch.setattr(material_module, "generate_structured", lambda *a, **k: {
        "idea": "Velocity changes by the same amount each second.",
        "points": [
            {"title": "Equal changes", "body": "The velocity changes by the same "
                                               "amount in every second of the motion."},
            {"title": "Units", "body": "Metres per second squared multiplied by "
                                       "seconds leaves metres per second."},
        ],
        "check": {"question": "q", "answer": "The units matter."},
    })
    out = material_module.teach(db, row.id, note.id)
    assert [p["paragraph"] for p in out["points"]] == [0, 2]


def test_a_check_question_the_notes_cannot_answer_is_removed(db, subject, monkeypatch):
    """Retrieval practice only works if the answer is findable. Being asked
    something your own notes don't answer teaches you that your notes are
    useless, which is the opposite of the point."""
    row, note = subject
    monkeypatch.setattr(material_module, "generate_structured", lambda *a, **k: {
        "idea": "Velocity changes by the same amount each second.",
        "points": [{"title": "Equal changes",
                    "body": "The velocity changes by the same amount in every second."}],
        "check": {"question": "What is the terminal velocity of a skydiver?",
                  "answer": "Around 55 metres per second once drag balances weight."},
    })
    out = material_module.teach(db, row.id, note.id)
    assert out["check"] is None
    assert out["points"], "dropping the question must not drop the lesson"


def test_the_memory_hook_is_allowed_to_be_new_wording(db, subject, monkeypatch):
    """The analogy is the one invented thing, and it has to be: an analogy built
    only from words already in the note is just the note again. It is labelled
    as Ollie's own in the UI, so it is not passed off as course content."""
    row, note = subject
    monkeypatch.setattr(material_module, "generate_structured", lambda *a, **k: {
        "idea": "Velocity changes by the same amount each second.",
        "points": [{"title": "Equal changes",
                    "body": "The velocity changes by the same amount in every second."}],
        "memory_hook": "Picture a staircase where every step is the same height.",
        "check": None,
    })
    out = material_module.teach(db, row.id, note.id)
    assert "staircase" in out["memory_hook"]


def test_an_equation_quoted_from_the_note_is_not_dropped(db, subject, monkeypatch):
    """The hole word overlap leaves, and in a maths course it is the important
    one. `v = u + a t` has no words longer than two characters, so overlap
    scores it zero and the floor would throw away the line the note is about.
    The verbatim fallback catches it; an equation the note does NOT contain
    still gets dropped, which is the next test."""
    row, note = subject
    formula = SubjectMaterial(
        subject_id=row.id, title="The equations", position=2,
        body="The three equations relate velocity, acceleration, displacement "
             "and time, and they hold for as long as the acceleration stays "
             "the same.\n\n"
             "v = u + a t\n\n"
             "Each one leaves out exactly one of the five quantities, so you "
             "pick the equation that omits the thing you were never given "
             "rather than solving for it first.\n")
    db.add(formula)
    db.commit()

    monkeypatch.setattr(material_module, "generate_structured", lambda *a, **k: {
        "idea": "Pick the equation that leaves out what you were not given.",
        "points": [{"title": "v = u + a t", "body": "v = u + a t"}],
        "check": None,
    })
    out = material_module.teach(db, row.id, formula.id)
    assert out["dropped"] == 0
    assert out["points"][0]["body"] == "v = u + a t"


def test_an_equation_the_note_never_states_is_still_dropped(db, subject, monkeypatch):
    """The fallback must not become a hole of its own: it rescues a quotation,
    not an invention that happens to be short."""
    row, note = subject
    monkeypatch.setattr(material_module, "generate_structured", lambda *a, **k: {
        "idea": "Velocity changes by the same amount each second.",
        "points": [
            {"title": "Equal changes", "body": "The velocity changes by the "
                                               "same amount in every second."},
            {"title": "E = m c^2", "body": "E = m c^2"},
        ],
        "check": None,
    })
    out = material_module.teach(db, row.id, note.id)
    assert [p["title"] for p in out["points"]] == ["Equal changes"]
    assert out["dropped"] == 1


# --------------------------------------------------------------------------
# the refusal
# --------------------------------------------------------------------------
def test_an_empty_note_gets_no_lesson_and_no_model_call(db, subject, monkeypatch):
    """Guarantee 1: there is no prompt to argue with, because no prompt is sent."""
    row, _ = subject
    stub = SubjectMaterial(subject_id=row.id, title="Chapter 4", body="TODO", position=1)
    db.add(stub)
    db.commit()

    def explode(*_args, **_kwargs):
        raise AssertionError("a model was asked to teach from an empty note")

    monkeypatch.setattr(material_module, "generate_structured", explode)
    out = material_module.teach(db, row.id, stub.id)
    assert out["taught"] is False
    assert out["points"] == []
    assert "Chapter 4" in out["idea"]


def test_a_lesson_with_nothing_grounded_left_is_not_called_a_lesson(db, subject, monkeypatch):
    row, note = subject
    monkeypatch.setattr(material_module, "generate_structured", lambda *a, **k: {
        "idea": "Momentum is conserved in every collision.",
        "points": [{"title": "Elastic collisions",
                    "body": "In an elastic collision the kinetic energy before "
                            "equals the kinetic energy afterwards."}],
        "check": None,
    })
    out = material_module.teach(db, row.id, note.id)
    assert out["taught"] is False
    assert out["points"] == []


def test_a_note_from_another_subject_cannot_be_taught(db, subject):
    """Subject ids come off the URL, so the pairing is checked rather than
    trusted."""
    row, note = subject
    other = Subject(course_id="teach-test", name="Programming", checker="python")
    db.add(other)
    db.commit()
    with pytest.raises(LookupError):
        material_module.teach(db, other.id, note.id)


# --------------------------------------------------------------------------
# the offline stub, which the demo runs on
# --------------------------------------------------------------------------
def test_the_offline_stub_produces_a_lesson_that_survives_the_floor(db, subject):
    """The stub quotes the note verbatim, so it clears the grounding floor by
    construction. If this breaks, every offline demo of the Learn page shows an
    empty lesson."""
    row, note = subject
    out = material_module.teach(db, row.id, note.id)
    assert out["taught"] is True
    assert len(out["points"]) >= 2
    assert out["dropped"] == 0
    assert out["check"] and out["check"]["answer"]


def test_the_api_serves_the_lesson_and_404s_on_a_bad_pairing(client):
    subjects = client.get("/api/courses/CS101/subjects").json()
    assert subjects, "the seed should create subjects"
    with_notes = next(s for s in subjects if s["materials"])
    notes = client.get(f"/api/subjects/{with_notes['id']}/materials").json()

    out = client.post(
        f"/api/subjects/{with_notes['id']}/materials/{notes[0]['id']}/teach").json()
    assert out["title"] == notes[0]["title"]
    assert out["taught"] is True

    missing = client.post(
        f"/api/subjects/{with_notes['id']}/materials/999999/teach")
    assert missing.status_code == 404


def test_every_seeded_note_actually_teaches(client):
    """The failure this catches is silent. A note that no point can be grounded
    against still renders — as an empty lesson with an apology in it — and
    nothing in the logs says so. Checking every seeded note means a note added
    later that reads fine but teaches nothing is caught here rather than on
    screen in front of a class."""
    subjects = client.get("/api/courses/CS101/subjects").json()
    taught = 0
    for subject in subjects:
        for note in client.get(f"/api/subjects/{subject['id']}/materials").json():
            out = client.post(
                f"/api/subjects/{subject['id']}/materials/{note['id']}/teach").json()
            assert out["taught"] is True, f"{subject['name']} / {note['title']}"
            assert len(out["points"]) >= 2, f"{subject['name']} / {note['title']}"
            assert out["dropped"] == 0, (
                f"{subject['name']} / {note['title']} lost {out['dropped']} point(s) "
                f"to the grounding floor")
            taught += 1
    assert taught >= 8, "both subjects should have notes to teach from"


def test_both_subjects_have_notes(client):
    """Learn is closed on a subject with no notes, so a subject losing its
    material is a feature disappearing, not a cosmetic change."""
    subjects = {s["name"]: s for s in client.get("/api/courses/CS101/subjects").json()}
    assert subjects["Programming"]["materials"] >= 5
    assert subjects["Mechanics"]["materials"] >= 3


def test_seeding_again_adds_notes_without_duplicating_or_overwriting(client, db):
    """The seed skips a subject it has seen before, which is right for the
    subject's own fields but once meant a note written later could only ever
    reach a brand new course. The backfill is additive and matched on title: an
    instructor's edit to a seeded note must survive the next seed, and nothing
    may be duplicated."""
    from app.models import Subject, SubjectMaterial
    from app.seed import seed

    subject = db.query(Subject).filter(Subject.name == "Programming").one()
    before = len(subject.materials)

    edited = subject.materials[0]
    edited.body = edited.body + "\n\nAdded by the instructor."
    own = SubjectMaterial(subject_id=subject.id, title="My own handout",
                          body="Notes I wrote myself.", position=99)
    db.add(own)
    db.commit()

    seed()
    db.expire_all()

    subject = db.query(Subject).filter(Subject.name == "Programming").one()
    titles = [m.title for m in subject.materials]
    assert len(titles) == len(set(titles)), "a second seed duplicated a note"
    assert len(subject.materials) == before + 1, "a second seed changed the roster"
    assert "My own handout" in titles, "the instructor's own note was removed"
    assert "Added by the instructor." in subject.materials[0].body, \
        "the instructor's edit was overwritten"


def test_the_material_list_carries_a_length_for_the_learn_page(client):
    subjects = client.get("/api/courses/CS101/subjects").json()
    with_notes = next(s for s in subjects if s["materials"])
    notes = client.get(f"/api/subjects/{with_notes['id']}/materials").json()
    assert all(n["words"] > 0 for n in notes)
