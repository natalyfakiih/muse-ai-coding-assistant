"""Sandboxed execution for the Reflection Engine's behavioral diff.

Running student code is a correctness/safety requirement regardless of scale,
so every case runs in a fresh subprocess with a wall-clock timeout and — on
POSIX — CPU, address-space and file-size limits. On Windows `resource` and
`preexec_fn` do not exist, so the timeout plus a scratch working directory is
what you get; run the grader on Linux (or in a container) for the real thing.

No LLM is involved here. This is plain test execution.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, asdict
from pathlib import Path

from app.config import settings

POSIX = os.name == "posix"

_RUNNER = '''
import ast, json, sys, os, io, contextlib

# -I keeps the script directory off sys.path; the student's file lives there.
sys.path.insert(0, os.getcwd())
sys.setrecursionlimit(3000)
spec_args = json.loads(sys.argv[1])
entry_point = sys.argv[2]

stdout_buffer = io.StringIO()
result = {"ok": False, "value_repr": None, "value_type": None,
          "error": None, "stdout": ""}
try:
    with contextlib.redirect_stdout(stdout_buffer):
        import solution
        target = getattr(solution, entry_point, None)
        if target is None:
            raise AttributeError(
                "the specification names %r but your file does not define it" % entry_point
            )
        value = target(*spec_args["args"], **spec_args["kwargs"])
    result["ok"] = True
    result["value_repr"] = repr(value)
    result["value_type"] = type(value).__name__
except BaseException as exc:
    result["error"] = "%s: %s" % (type(exc).__name__, exc)
result["stdout"] = stdout_buffer.getvalue()[:2000]
sys.stderr.write("\\n__MUSE_RESULT__" + json.dumps(result))
'''


@dataclass
class RunResult:
    ok: bool
    value_repr: str | None = None
    value_type: str | None = None
    error: str | None = None
    stdout: str = ""
    timed_out: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# Argument parsing: the specification's examples are written by students, in
# call-argument form ("n=3", "items=[], k=2", or bare "3").
# --------------------------------------------------------------------------
def entry_arity(code: str, entry_point: str) -> int | None:
    """How many positional parameters the entry point declares — or None when it
    takes *args, where any number is legitimate and nothing should be reshaped."""
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == entry_point:
            if node.args.vararg is not None:
                return None
            return len(getattr(node.args, "posonlyargs", [])) + len(node.args.args)
    return None


def parse_call_args(input_spec: str, arity: int | None = None) -> tuple[list, dict]:
    """Students write examples the way they'd say them out loud. "1,1,2" for a
    one-parameter function is one list with the brackets left off, not three
    arguments — and reading it as three is how a correct `dedupe(items)` gets
    called as `dedupe(1, 1, 2)` and fails on a TypeError the student can do
    nothing about except deform their function to match.
    """
    text = (input_spec or "").strip()
    if not text:
        return [], {}
    try:
        call = ast.parse(f"_f({text})", mode="eval").body
        if isinstance(call, ast.Call):
            args = [ast.literal_eval(a) for a in call.args]
            kwargs = {kw.arg: ast.literal_eval(kw.value) for kw in call.keywords if kw.arg}
            if arity == 1 and len(args) > 1 and not kwargs:
                args = [list(args)]
            return args, kwargs
    except (SyntaxError, ValueError):
        pass
    try:  # bare literal, e.g. "3" or "[1, 2]"
        return [ast.literal_eval(text)], {}
    except (SyntaxError, ValueError):
        return [text], {}


def parse_expected(expected: str | None):
    """Returns (value, parsed_ok). Unparseable expectations fall back to a
    normalised string comparison rather than being silently dropped."""
    if expected is None:
        return None, False
    text = str(expected).strip()
    try:
        return ast.literal_eval(text), True
    except (SyntaxError, ValueError):
        return text, False


def matches(actual_repr: str | None, expected_raw: str | None) -> bool:
    if expected_raw is None or actual_repr is None:
        return False
    expected_value, parsed = parse_expected(expected_raw)
    if parsed:
        try:
            actual_value = ast.literal_eval(actual_repr)
        except (SyntaxError, ValueError):
            return str(actual_repr).strip() == str(expected_raw).strip()
        if isinstance(actual_value, bool) != isinstance(expected_value, bool):
            return False
        # "1,2" written as an expected output parses as the tuple (1, 2). A
        # student who wrote it meant the sequence, not the container type — and
        # the container type is separately checked against the declared output
        # type, so failing here would report the same thing twice and, worse,
        # push them into returning a tuple.
        if isinstance(actual_value, (list, tuple)) and isinstance(expected_value, (list, tuple)):
            return list(actual_value) == list(expected_value)
        try:
            return actual_value == expected_value
        except Exception:
            return False
    return _normalise(actual_repr) == _normalise(str(expected_raw))


def _normalise(text: str) -> str:
    return "".join(str(text).split()).strip("'\"").lower()


# --------------------------------------------------------------------------
def _limits():  # pragma: no cover - POSIX only
    import resource

    memory = settings.SANDBOX_MEMORY_MB * 1024 * 1024
    cpu = max(1, settings.SANDBOX_TIMEOUT_SECONDS)
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024, 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
    os.setsid()


def run_case(code: str, entry_point: str, input_spec: str) -> RunResult:
    """Run one declared case against the student's code."""
    args, kwargs = parse_call_args(input_spec, arity=entry_arity(code, entry_point))

    with tempfile.TemporaryDirectory(prefix="muse-run-") as workdir:
        path = Path(workdir)
        (path / "solution.py").write_text(code or "", encoding="utf-8")
        (path / "_runner.py").write_text(_RUNNER, encoding="utf-8")

        env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "HOME": workdir,
        }
        if os.name == "nt":  # Windows needs a couple of variables to start python
            for key in ("SYSTEMROOT", "SYSTEMDRIVE", "TEMP", "TMP", "PATHEXT", "COMSPEC"):
                if key in os.environ:
                    env[key] = os.environ[key]

        try:
            proc = subprocess.run(
                [sys.executable, "-I", "-S", "_runner.py",
                 json.dumps({"args": args, "kwargs": kwargs}), entry_point],
                cwd=workdir,
                env=env,
                capture_output=True,
                text=True,
                timeout=settings.SANDBOX_TIMEOUT_SECONDS,
                preexec_fn=_limits if POSIX else None,
            )
        except subprocess.TimeoutExpired:
            return RunResult(ok=False, error="timed out", timed_out=True)
        except Exception as exc:  # noqa: BLE001
            return RunResult(ok=False, error=f"sandbox failure: {type(exc).__name__}: {exc}")

    marker = "__MUSE_RESULT__"
    stderr = proc.stderr or ""
    if marker not in stderr:
        # A negative return code means a signal: SIGXCPU (CPU rlimit) and
        # SIGKILL are the two the sandbox itself causes.
        if proc.returncode and proc.returncode < 0:
            import signal

            try:
                name = signal.Signals(-proc.returncode).name
            except ValueError:
                name = str(-proc.returncode)
            if name in ("SIGXCPU", "SIGKILL", "SIGXFSZ"):
                return RunResult(
                    ok=False, timed_out=name == "SIGXCPU",
                    error=f"exceeded the sandbox limit ({name}) — likely an infinite loop "
                          f"or runaway allocation",
                )
            return RunResult(ok=False, error=f"process killed by {name}")
        detail = (stderr or proc.stdout or "no output").strip()[-500:]
        return RunResult(ok=False, error=f"process died before returning a result: {detail}")

    try:
        payload = json.loads(stderr.split(marker, 1)[1])
    except json.JSONDecodeError:
        return RunResult(ok=False, error="unreadable sandbox result")

    return RunResult(
        ok=bool(payload.get("ok")),
        value_repr=payload.get("value_repr"),
        value_type=payload.get("value_type"),
        error=payload.get("error"),
        stdout=(payload.get("stdout") or "")[: settings.SANDBOX_MAX_OUTPUT_BYTES],
    )

# --------------------------------------------------------------------------
# Expression comparison, for subjects whose answer is a value rather than a
# program. Same hardening as run_case: a fresh subprocess, a wall-clock
# timeout and the POSIX resource limits. Student input is never parsed in the
# server process — sympy's parser builds and evaluates expression trees, and a
# crafted input can hang it, which is exactly what the CPU limit is for.
# --------------------------------------------------------------------------
_MATH_RUNNER = '''
import json, sys

payload = json.loads(sys.argv[1])
result = {"ok": False, "equivalent": None, "mode": None,
          "student_value": None, "expected_value": None, "error": None}
try:
    import re
    from sympy import simplify, N, Abs
    from sympy.parsing.sympy_parser import (
        parse_expr, standard_transformations,
        implicit_multiplication_application, convert_xor,
    )
    T = standard_transformations + (implicit_multiplication_application, convert_xor)

    # parse_expr() ends in eval(). Python puts __builtins__ into any globals
    # dict handed to eval unless it is already there, so an "answer" of
    # __import__("os").system(...) really does run. Verified: a crafted answer
    # wrote a file and read /etc/hostname before this was locked down.
    #
    # Two independent locks, because one of them will eventually be wrong:
    #   1. a character whitelist, so the dangerous spellings never reach eval
    #   2. an empty __builtins__, so they would do nothing if they did
    SAFE = re.compile(r"^[0-9A-Za-z_+\-*/^().,!<>= \t]*$")

    NAMESPACE = {}
    exec("from sympy import *", NAMESPACE)
    NAMESPACE["__builtins__"] = {}

    def read(text):
        text = str(text)
        if len(text) > 500:
            raise ValueError("answer is too long to be an expression")
        if not SAFE.match(text):
            raise ValueError("answer contains characters that are not maths")
        if "__" in text or "lambda" in text:
            raise ValueError("answer contains something that is not maths")
        return parse_expr(text, transformations=T, global_dict=NAMESPACE,
                          evaluate=True)

    student = read(payload["student"])
    expected = read(payload["expected"])
    result["student_value"] = str(student)
    result["expected_value"] = str(expected)

    # Exact first: 1/2 and 0.5 and sqrt(4)/4 should all count as the same
    # answer when they genuinely are.
    if simplify(student - expected) == 0:
        result.update(ok=True, equivalent=True, mode="symbolic")
    else:
        # Then numerically, because a student who writes 1.5708 for pi/2 has
        # the right answer and symbolic equality will say they do not.
        tol = payload.get("tolerance")
        if tol is not None:
            diff = Abs(N(student) - N(expected))
            close = bool(diff <= N(tol))
            result.update(ok=True, equivalent=close, mode="numeric")
        else:
            result.update(ok=True, equivalent=False, mode="symbolic")
except BaseException as exc:
    result["error"] = "%s: %s" % (type(exc).__name__, exc)

sys.stderr.write("\\n__MUSE_RESULT__" + json.dumps(result))
'''


@dataclass
class MathResult:
    ok: bool
    equivalent: bool | None = None
    mode: str | None = None
    student_value: str | None = None
    expected_value: str | None = None
    error: str | None = None
    timed_out: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def run_expression(student: str, expected: str, tolerance: float | None = None) -> MathResult:
    """Is the student's answer the same value as the expected one?"""
    if not str(student or "").strip():
        return MathResult(ok=False, error="no answer given")

    with tempfile.TemporaryDirectory(prefix="muse-math-") as workdir:
        path = Path(workdir)
        (path / "_math.py").write_text(_MATH_RUNNER, encoding="utf-8")

        env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "HOME": workdir,
        }
        if os.name == "nt":
            for key in ("SYSTEMROOT", "SYSTEMDRIVE", "TEMP", "TMP", "PATHEXT", "COMSPEC"):
                if key in os.environ:
                    env[key] = os.environ[key]

        try:
            # No -S here: sympy lives in site-packages, and -S would keep it off
            # sys.path. -I still isolates the environment and the user site.
            proc = subprocess.run(
                [sys.executable, "-I", "_math.py",
                 json.dumps({"student": student, "expected": expected,
                             "tolerance": tolerance})],
                cwd=workdir, env=env, capture_output=True, text=True,
                timeout=settings.SANDBOX_TIMEOUT_SECONDS,
                preexec_fn=_limits if POSIX else None,
            )
        except subprocess.TimeoutExpired:
            return MathResult(ok=False, error="timed out", timed_out=True)
        except Exception as exc:  # noqa: BLE001
            return MathResult(ok=False, error=f"sandbox failure: {type(exc).__name__}: {exc}")

    marker = "__MUSE_RESULT__"
    stderr = proc.stderr or ""
    if marker not in stderr:
        return MathResult(ok=False, error=(stderr or "no result").strip()[:300])
    try:
        payload = json.loads(stderr.split(marker, 1)[1])
    except ValueError:
        return MathResult(ok=False, error="unreadable result")
    return MathResult(**payload)
