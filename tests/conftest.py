import os
import tempfile
from pathlib import Path

# Must be set before app.config is imported anywhere.
_TMP = Path(tempfile.mkdtemp(prefix="muse-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP / 'test.db'}"
os.environ["USE_FAKE_LLM"] = "1"
os.environ["SANDBOX_TIMEOUT_SECONDS"] = "5"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed  # noqa: E402


@pytest.fixture()
def db():
    """A read/write session on whatever the current test has set up. It does
    not reset the schema — `client` owns that, and dropping here would wipe the
    seed out from under a test that uses both fixtures."""
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client():
    from app.main import flush_call_log

    # The provider log writes on a background thread; let it settle before the
    # schema is dropped, or its rows land in the next test's fresh table.
    flush_call_log(2.0)
    Base.metadata.drop_all(bind=engine)
    seed()
    with TestClient(app) as test_client:
        yield test_client
    flush_call_log(2.0)


@pytest.fixture()
def seeded(client):
    """(client, student_id, assignment_id)"""
    student_id = client.get("/api/students").json()[0]["id"]
    assignment = client.get("/api/assignments").json()[0]
    return client, student_id, assignment["id"]


BUGGY_FIB = """
def fib(n):
    out = []
    a, b = 0, 1
    for _ in range(n + 1):
        out.append(a)
        a, b = b, a + b
    return out
"""

GOOD_FIB = """
def fib(n):
    out = []
    a, b = 0, 1
    for _ in range(max(0, n)):
        out.append(a)
        a, b = b, a + b
    return out
"""


def lock_specification(client, student_id, assignment_id):
    """Walk the gate until it locks, then force-lock if it is still open."""
    messages = [
        "I want a function that returns the first n fibonacci numbers.",
        "It takes an int n and returns a list of ints. n=3 -> [0, 1, 1]",
        "For n = 0 it should return an empty list.",
        "Negative n should also return an empty list.",
    ]
    result = {}
    for message in messages:
        result = client.post(
            f"/api/assignments/{assignment_id}/specification",
            json={"student_id": student_id, "message": message},
        ).json()
        if result["locked"]:
            break
    if not result["locked"]:
        result = client.post(
            f"/api/assignments/{assignment_id}/specification/lock",
            json={"student_id": student_id},
        ).json()
    return result["specification_id"]
