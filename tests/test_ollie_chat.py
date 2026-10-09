"""Chat with Ollie: the endpoint answers on the offline stub and never stores
the conversation."""


def test_chat_replies_offline(client):
    res = client.post("/api/ollie/chat", json={
        "student_name": "Nataly",
        "messages": [{"role": "user", "content": "Quiz me on functions"}],
    })
    assert res.status_code == 200
    body = res.json()
    assert body["mode"] == "offline-stub"
    assert body["reply"].strip()


def test_chat_rejects_empty_conversation(client):
    res = client.post("/api/ollie/chat", json={"messages": []})
    assert res.status_code == 422


def test_chat_pages_are_served(client):
    for path in ("/", "/learning", "/progress", "/week", "/chat", "/learn"):
        assert client.get(path).status_code == 200, path


def test_chat_records_topic_not_text(client):
    """The teacher sees which note a question matched, never the question."""
    course = client.get("/api/assignments").json()[0]["course_id"]
    student = client.get("/api/students").json()[0]["id"]
    secret = "zebra-penguin-1234 what does return hand back from a function"
    assert client.post("/api/ollie/chat", json={
        "student_id": student, "course_id": course,
        "messages": [{"role": "user", "content": secret}],
    }).status_code == 200

    topics = client.get(f"/api/courses/{course}/analytics/ollie-topics").json()
    assert topics["total_questions"] == 1
    assert topics["students"] == 1
    assert "zebra-penguin" not in str(topics)


def _subject(client, checker):
    course = client.get("/api/assignments").json()[0]["course_id"]
    subjects = client.get(f"/api/courses/{course}/subjects").json()
    return course, next(s for s in subjects if s["checker"] == checker)


def test_teacher_can_add_a_programming_exercise_under_a_subject(client):
    course, subject = _subject(client, "python")
    res = client.post("/api/assignments", json={
        "course_id": course, "subject_id": subject["id"],
        "prompt_text": "Write a function that doubles a number.",
        "entry_point": "double", "items": [{"text": "must state the return type"}],
    })
    assert res.status_code == 201, res.text
    made = res.json()
    assert made["subject_id"] == subject["id"] and made["entry_point"] == "double"
    # It shows up where the student pages look for it.
    assert any(a["id"] == made["id"] and a["subject_id"] == subject["id"]
               for a in client.get("/api/assignments").json())


def test_maths_exercise_needs_an_answer_not_a_function(client):
    course, subject = _subject(client, "math")
    base = {"course_id": course, "subject_id": subject["id"],
            "prompt_text": "A ball falls 5 m from rest. How fast is it going?",
            "items": [{"text": "must state what you are solving for"}]}
    assert client.post("/api/assignments", json=base).status_code == 400
    res = client.post("/api/assignments", json={**base, "expected_answer": "9.9 m/s"})
    assert res.status_code == 201, res.text
    assert res.json()["entry_point"] is None
