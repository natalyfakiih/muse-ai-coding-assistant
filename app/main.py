"""MUSE — an AI-assisted programming tutor that enforces three phases:
Specification, Guided Implementation, Reflection.

Run:  uvicorn app.main:app --reload
"""
from __future__ import annotations

import atexit
import logging
import queue
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.db import SessionLocal, init_db
from app.llm.client import (
    CallRecord,
    MalformedJSONError,
    NoProviderConfiguredError,
    ProviderExhaustedError,
    register_call_logger,
)
from app.models import LLMCall
from app.modules.profiler import ProfileNotVisible

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("muse")

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"


# Provider-call logging runs on its own thread. Writing inline would put a
# second transaction inside a student's request — which deadlocks SQLite and
# adds latency on Postgres — and observability must never be able to fail a turn.
_call_queue: queue.Queue[CallRecord | None] = queue.Queue(maxsize=10_000)


def _persist_call(record: CallRecord) -> None:
    try:
        _call_queue.put_nowait(record)
    except queue.Full:
        logger.warning("llm call log queue full — dropping a record")


def _drain_call_queue() -> None:
    while True:
        record = _call_queue.get()
        if record is None:
            _call_queue.task_done()
            return
        session = SessionLocal()
        try:
            session.add(LLMCall(
                task=record.task, tier=record.tier, provider=record.provider,
                model=record.model, latency_ms=record.latency_ms, success=record.success,
                fell_back=record.fell_back, attempts=record.attempts,
                error_type=record.error_type, error_detail=record.error_detail,
            ))
            session.commit()
        except Exception:
            session.rollback()
            logger.exception("failed to persist llm call record")
        finally:
            session.close()
            _call_queue.task_done()


def flush_call_log(timeout: float = 5.0) -> None:
    """Tests and the ops view want the log settled before they read it."""
    deadline = time.monotonic() + timeout
    while _call_queue.unfinished_tasks and time.monotonic() < deadline:
        time.sleep(0.02)


_writer_thread: threading.Thread | None = None


def _start_call_writer() -> None:
    global _writer_thread
    if _writer_thread and _writer_thread.is_alive():
        return
    _writer_thread = threading.Thread(target=_drain_call_queue, name="llm-call-log", daemon=True)
    _writer_thread.start()
    atexit.register(lambda: _call_queue.put(None))


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _start_call_writer()
    register_call_logger(_persist_call)
    if not settings.llm_enabled:
        logger.warning(
            "No provider keys configured — running on the deterministic offline "
            "stub. Set MISTRAL_API_KEY and GROQ_API_KEY in .env for real calls."
        )
    else:
        logger.info(
            "Providers: primary=%s classification=%s/%s dialogue=%s/%s",
            settings.PRIMARY_PROVIDER, settings.MISTRAL_MODEL, settings.GROQ_FALLBACK_MODEL,
            settings.DIALOGUE_MISTRAL_MODEL, settings.DIALOGUE_GROQ_MODEL,
        )
    yield


app = FastAPI(
    title="MUSE",
    description="Specification Gate → Tutor Layer → Reflection Engine → Instructor Dashboard",
    version="0.1.0",
    lifespan=lifespan,
)

from app.api import dashboard, reflection, specifications, tutoring  # noqa: E402

app.include_router(specifications.router, prefix="/api")
app.include_router(tutoring.router, prefix="/api")
app.include_router(reflection.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api")


# --------------------------------------------------------------------------
# Error handling — provider trouble is a 503, a broken JSON contract is a 502,
# and neither should ever surface to a student as a stack trace.
# --------------------------------------------------------------------------
@app.exception_handler(ProviderExhaustedError)
async def _exhausted(request: Request, exc: ProviderExhaustedError):
    logger.error("provider exhausted: %s", exc)
    return JSONResponse(
        status_code=503,
        content={"detail": "Both AI providers are rate-limited right now. "
                           "Wait a few seconds and send that again.",
                 "error": "provider_exhausted"},
    )


@app.exception_handler(MalformedJSONError)
async def _malformed(request: Request, exc: MalformedJSONError):
    logger.error("malformed model output from %s: %s", exc.provider, exc.raw[:400])
    return JSONResponse(
        status_code=502,
        content={"detail": "The model returned an unreadable response. This has been logged.",
                 "error": "malformed_model_output", "provider": exc.provider},
    )


@app.exception_handler(NoProviderConfiguredError)
async def _no_provider(request: Request, exc: NoProviderConfiguredError):
    return JSONResponse(status_code=503,
                        content={"detail": str(exc), "error": "no_provider_configured"})


@app.exception_handler(ProfileNotVisible)
async def _not_visible(request: Request, exc: ProfileNotVisible):
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(ValueError)
async def _value_error(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(LookupError)
async def _lookup_error(request: Request, exc: LookupError):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


# --------------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "llm": "live" if settings.llm_enabled else "offline-stub",
        "primary_provider": settings.PRIMARY_PROVIDER,
        "classification_models": {"mistral": settings.MISTRAL_MODEL,
                                  "groq": settings.GROQ_FALLBACK_MODEL},
        "dialogue_models": {"mistral": settings.DIALOGUE_MISTRAL_MODEL,
                            "groq": settings.DIALOGUE_GROQ_MODEL},
        "gate_max_asks_per_gap": settings.GATE_MAX_ASKS_PER_GAP,
        "escape_hatch_rounds": settings.TUTOR_ESCAPE_HATCH_ROUNDS,
    }


if FRONTEND.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND)), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(str(FRONTEND / "index.html"))

    @app.get("/instructor", include_in_schema=False)
    def instructor():
        return FileResponse(str(FRONTEND / "instructor.html"))
