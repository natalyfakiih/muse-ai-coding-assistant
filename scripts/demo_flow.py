"""End-to-end walkthrough of one student's assignment, printed step by step.

    python scripts/demo_flow.py

Runs against the offline stub by default so it costs nothing; set
USE_FAKE_LLM=0 with real keys in .env to watch the real providers work.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("USE_FAKE_LLM", "1")
os.environ.setdefault("DATABASE_URL", "sqlite:///./muse_demo.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed  # noqa: E402

BUGGY = '''
def fib(n):
    out = []
    a, b = 0, 1
    for _ in range(n + 1):   # off by one
        out.append(a)
        a, b = b, a + b
    return out
'''


def head(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def main() -> None:
    Base.metadata.drop_all(bind=engine)
    seed()

    with TestClient(app) as client:
        student_id = client.get("/api/students").json()[0]["id"]
        assignment = client.get("/api/assignments").json()[0]
        aid = assignment["id"]

        head("PHASE 1 — Specification Gate")
        messages = [
            "I want to write a function that returns the first n fibonacci numbers.",
            "It takes an int n and returns a list of ints. n=3 -> [0, 1, 1]",
            "For n = 0 it should return an empty list.",
            "Negative n should also return an empty list.",
        ]
        result = {}
        for message in messages:
            result = client.post(f"/api/assignments/{aid}/specification",
                                 json={"student_id": student_id, "message": message}).json()
            print(f"\n  student : {message}")
            print(f"  muse    : {result.get('question') or result.get('message')}")
            print(f"  (v{result['version']}, locked={result['locked']}, "
                  f"gap={(result.get('gap') or {}).get('id')})")
            if result["locked"]:
                break

        if not result["locked"]:
            print("\n  -> gate still open; student confirms their answer stands")
            result = client.post(f"/api/assignments/{aid}/specification/lock",
                                 json={"student_id": student_id}).json()
            print(f"  muse    : {result['message']}")

        spec_id = result["specification_id"]
        spec = client.get(f"/api/assignments/{aid}/specification",
                          params={"student_id": student_id}).json()
        print(f"\n  locked specification (v{spec['version']}):")
        for key, value in spec["content"].items():
            print(f"    {key:20} {value}")

        head("PHASE 2 — Tutor Layer")
        client.post(f"/api/specifications/{spec_id}/snapshots", json={"code": BUGGY})

        for message in [
            "what does .append() return?",
            "why is my fib loop returning one too many numbers?",
            "I checked and out has 4 items when n is 3.",
            "still not seeing it",
        ]:
            reply = client.post(f"/api/specifications/{spec_id}/interactions",
                                json={"message": message, "code": BUGGY}).json()
            print(f"\n  student : {message}")
            print(f"  [{reply['classified_type']} / {reply['response_mode']}"
                  f"{' / OVERRIDDEN' if reply['classifier_overridden'] else ''}]"
                  f" round {reply['thread_round']}")
            print(f"  muse    : {reply['response']}")
            if reply["escape_hatch_available"]:
                print("  -> escape hatch now available")

        hatch = client.post(f"/api/specifications/{spec_id}/interactions/escape-hatch",
                            json={}).json()
        print(f"\n  student takes the hint (assist cost {hatch['assist_cost']}, "
              f"after {hatch['rounds_before_hint']} rounds)")
        print(f"  muse    : {hatch['response']}")

        head("PHASE 3 — Reflection Engine")
        submission = client.post(f"/api/specifications/{spec_id}/submit",
                                 json={"code": BUGGY}).json()
        print(f"  cases run: {submission['cases_run']}, "
              f"passed: {submission['cases_passed']}")
        for case in submission["results"]:
            mark = "PASS" if case["passed"] else "FAIL"
            print(f"    [{mark}] {case['source']}  ->  {case['actual'] or case['error']}")

        print(f"\n  {submission['explanations_required']} discrepancy/ies need an explanation:")
        for diff in submission["diff_results"]:
            print(f"    ({diff['category']}) {diff['description']}")

        status = client.get(f"/api/specifications/{spec_id}/submission-status").json()
        print(f"\n  can finalize before explaining? {status['can_finalize']}")
        refused = client.post(f"/api/specifications/{spec_id}/finalize")
        print(f"  finalize attempt -> {refused.status_code}: {refused.json()['detail']}")

        for diff in submission["diff_results"]:
            client.post(f"/api/diff-results/{diff['id']}/explanation",
                        json={"explanation": "I looped one extra time because I used "
                                             "range(n + 1) instead of range(n)."})
        final = client.post(f"/api/specifications/{spec_id}/finalize").json()
        print(f"  after explanations -> finalized at {final['finalized_at']}")

        head("PHASE 4 — Instructor Dashboard")
        patterns = client.get("/api/courses/CS101/analytics/class-patterns").json()
        print(f"  students: {patterns['students']}  "
              f"question mix: {patterns['question_mix']}  "
              f"diff totals: {patterns['diff_category_totals']}")
        for pattern in patterns["diff_patterns"]:
            print(f"    {pattern['share_of_class'] * 100:.0f}% of the class — "
                  f"{pattern['category']} on {pattern['spec_item']}")

        summary = client.get(f"/api/students/{student_id}/narrative-summary").json()
        print(f"\n  narrative summary: {summary['summary']}")

        profile = client.get(f"/api/students/{student_id}/dependency-profile").json()
        print(f"\n  dependency profile (viewer=student, visibility={profile['visibility']}):")
        print(f"    {profile['problem_solving']} problem-solving / {profile['factual']} factual, "
              f"escape hatch x{profile['escape_hatch_uses']}, "
              f"longest thread {profile['longest_unresolved_thread']}")
        blocked = client.get(f"/api/students/{student_id}/dependency-profile",
                             params={"viewer": "instructor"})
        print(f"    instructor view -> {blocked.status_code} (course has not opted in)")

        head("PROVIDER LOG")
        ops = client.get("/api/ops/llm-calls").json()
        print(f"  total calls: {ops['total_calls']}  by provider: {ops['calls_by_provider']}")
        print(f"  fallback rate: {ops['fallback_rate']}")
        print(f"  by task: {ops['calls_by_task']}")


if __name__ == "__main__":
    main()
