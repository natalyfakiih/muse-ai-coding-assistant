"""Burst load test for the Tutor Layer's classification path.

Simulates a class all typing at once — which is the moment Mistral's free-tier
requests-per-minute cap bites — and reports which provider actually served each
call. If Groq absorbs most of the burst, that is expected: that is what the
fallback is there for. If you see ProviderExhaustedError, both ceilings were hit
and you want a paid key for the demo.

    python scripts/loadtest.py                 # 20 students, 1 message each
    python scripts/loadtest.py --students 30 --messages 2
    python scripts/loadtest.py --tier dialogue # stress the expensive path

Runs against whatever .env configures. With no keys it exercises the offline
stub, which tells you nothing about rate limits — set real keys first.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.llm import client as llm  # noqa: E402
from app.llm import prompts  # noqa: E402

MESSAGES = [
    "what does .append() return?",
    "why is my loop off by one?",
    "how do I write a dict comprehension?",
    "my function returns None instead of a list",
    "what's the difference between a tuple and a list?",
    "Traceback (most recent call last): IndexError: list index out of range",
    "is range inclusive of the end value?",
    "my fib function gives 4 numbers when n is 3",
]

CODE_DIFF = """@@ -1,5 +1,6 @@
 def fib(n):
     out = []
     a, b = 0, 1
-    for _ in range(n):
+    for _ in range(n + 1):
         out.append(a)
"""

lock = threading.Lock()
records: list[llm.CallRecord] = []


def one_call(index: int, tier: str) -> dict:
    system, schema = prompts.CLASSIFY_QUESTION if tier == "classification" else prompts.SOCRATIC_QUESTION
    payload = json.dumps({
        "student_message": random.choice(MESSAGES),
        "current_code_diff": CODE_DIFF,
        "relevant_code_snippet": CODE_DIFF,
        "prior_counter_questions_this_thread": [],
    })
    started = time.perf_counter()
    try:
        llm.generate_structured(system, payload, schema,
                                task=f"loadtest_{tier}", tier=tier)
        return {"i": index, "ok": True, "ms": int((time.perf_counter() - started) * 1000)}
    except Exception as exc:  # noqa: BLE001
        return {"i": index, "ok": False, "error": type(exc).__name__,
                "ms": int((time.perf_counter() - started) * 1000)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--students", type=int, default=20)
    parser.add_argument("--messages", type=int, default=1)
    parser.add_argument("--tier", choices=["classification", "dialogue"], default="classification")
    parser.add_argument("--stagger", type=float, default=0.0,
                        help="seconds to spread the burst over (0 = all at once)")
    args = parser.parse_args()

    total = args.students * args.messages
    llm.register_call_logger(lambda record: (lock.acquire(), records.append(record), lock.release())[0])

    print(f"provider config: primary={settings.PRIMARY_PROVIDER} "
          f"mistral={settings.MISTRAL_MODEL} groq={settings.GROQ_FALLBACK_MODEL}")
    if not settings.llm_enabled:
        print("!! no API keys — this is exercising the offline stub and proves nothing "
              "about rate limits. Set MISTRAL_API_KEY / GROQ_API_KEY in .env.\n")
    print(f"firing {total} calls ({args.students} students x {args.messages}) "
          f"on the {args.tier} tier…\n")

    started = time.perf_counter()
    results = []
    with ThreadPoolExecutor(max_workers=args.students) as pool:
        futures = []
        for index in range(total):
            if args.stagger:
                time.sleep(args.stagger / total)
            futures.append(pool.submit(one_call, index, args.tier))
        for future in as_completed(futures):
            results.append(future.result())
    elapsed = time.perf_counter() - started

    ok = [r for r in results if r["ok"]]
    failed = [r for r in results if not r["ok"]]
    latencies = sorted(r["ms"] for r in ok)

    by_provider = Counter(r.provider for r in records)
    served = Counter(r.provider for r in records if r.success)
    errors = Counter(f"{r.provider}/{r.error_type}" for r in records if not r.success)
    fallbacks = sum(1 for r in records if r.fell_back and r.success)

    print(f"{'=' * 60}")
    print(f"{len(ok)}/{total} succeeded in {elapsed:.1f}s "
          f"({total / elapsed:.1f} req/s attempted)")
    if latencies:
        print(f"latency  p50 {latencies[len(latencies) // 2]}ms   "
              f"p95 {latencies[int(len(latencies) * 0.95) - 1]}ms   "
              f"max {latencies[-1]}ms")
    print(f"\nattempts by provider : {dict(by_provider)}")
    print(f"served by provider   : {dict(served)}")
    print(f"served via fallback  : {fallbacks} "
          f"({fallbacks / len(ok) * 100:.0f}% of successes)" if ok else "")
    if errors:
        print(f"failures             : {dict(errors)}")
    if failed:
        print(f"unserved requests    : {Counter(r['error'] for r in failed)}")

    print(f"\n{'=' * 60}")
    if fallbacks and not failed:
        print("Groq absorbed part of the burst — the fallback is doing its job.")
    elif failed:
        print("Both providers hit their ceiling. For a live demo, put a paid key on the "
              "classification tier (MUSE_PRIMARY=groq or a paid Mistral key) so the "
              "audience never sees a 429.")
    else:
        print("The primary served everything. Try --students 30 to find the ceiling.")


if __name__ == "__main__":
    main()
