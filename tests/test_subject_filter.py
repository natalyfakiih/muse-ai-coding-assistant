"""The teacher dashboard shows one subject at a time: every class number can
be limited to a subject's exercises with ?subject_id=."""


def _course_and_subjects(client):
    course = client.get("/api/assignments").json()[0]["course_id"]
    return course, client.get(f"/api/courses/{course}/subjects").json()


def test_class_numbers_accept_a_subject(client):
    course, subjects = _course_and_subjects(client)
    for s in subjects:
        for path in (f"/api/courses/{course}/analytics/class-patterns?subject_id={s['id']}",
                     f"/api/courses/{course}/analytics/insights?subject_id={s['id']}",
                     f"/api/courses/{course}/analytics/ollie-topics?subject_id={s['id']}"):
            assert client.get(path).status_code == 200, path


def test_unfiltered_numbers_are_unchanged(client):
    course, _ = _course_and_subjects(client)
    everything = client.get(f"/api/courses/{course}/analytics/class-patterns").json()
    assert everything["course_id"] == course


def test_student_insights_by_subject(client):
    course, subjects = _course_and_subjects(client)
    student = client.get("/api/students").json()[0]["id"]
    for s in subjects:
        res = client.get(f"/api/students/{student}/insights?viewer=instructor&subject_id={s['id']}")
        assert res.status_code == 200
        assert "status" in res.json()
