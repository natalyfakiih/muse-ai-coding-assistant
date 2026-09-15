"""The LLM abstraction layer.

Every AI call in MUSE goes through `generate_structured()`. No module imports
Mistral or Groq directly — that is the whole point of this file. When the
dialogue tier gets upgraded to a frontier model, a third branch is added here
and nothing in `app/modules/` changes.

Failure policy, deliberately narrow:
  * 429 / 5xx / timeout / connection error  -> retry Mistral briefly, then Groq.
  * malformed JSON from the model           -> raises MalformedJSONError. It is
    logged, not silently swallowed, because a model that stopped honouring the
    JSON contract is a prompt bug, not a capacity problem, and failing over to
    the other provider would just hide it.

Backoff is short on purpose: a student is sitting on a spinning cursor. Two
quick retries and then Groq beats thirty seconds of politeness toward Mistral.
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.config import settings

logger = logging.getLogger("muse.llm")

# --------------------------------------------------------------------------
# SDK imports. mistralai 2.x moved the client into `mistralai.client`; 1.x
# exports it at the top level. Support both rather than pinning a minor.
# --------------------------------------------------------------------------
try:  # pragma: no cover - import shape depends on installed SDK
    from mistralai import Mistral  # type: ignore
except ImportError:  # pragma: no cover
    try:
        from mistralai.client import Mistral  # type: ignore
    except ImportError:
        Mistral = None  # type: ignore

try:  # pragma: no cover
    from groq import Groq  # type: ignore
except ImportError:  # pragma: no cover
    Groq = None  # type: ignore


MISTRAL_MODEL = settings.MISTRAL_MODEL
GROQ_FALLBACK_MODEL = settings.GROQ_FALLBACK_MODEL

CLASSIFICATION = "classification"
DIALOGUE = "dialogue"


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------
class ProviderExhaustedError(RuntimeError):
    """Both providers were rate-limited or down."""


class MalformedJSONError(ValueError):
    """A provider answered, but not with parseable JSON. Not a failover case."""

    def __init__(self, provider: str, raw: str):
        self.provider = provider
        self.raw = raw
        super().__init__(f"{provider} returned non-JSON output: {raw[:280]!r}")


class NoProviderConfiguredError(RuntimeError):
    """No API keys and the offline stub is switched off."""


# --------------------------------------------------------------------------
# Call log sink — main.py swaps in a DB-backed writer at startup.
# --------------------------------------------------------------------------
@dataclass
class CallRecord:
    task: str
    tier: str
    provider: str
    model: str | None
    latency_ms: int
    success: bool
    fell_back: bool = False
    attempts: int = 1
    error_type: str | None = None
    error_detail: str | None = None


_sinks: list[Callable[[CallRecord], None]] = []


def register_call_logger(sink: Callable[[CallRecord], None]) -> None:
    # Idempotent: app startup runs once per process normally but more than once
    # under a test client, and a duplicate sink writes every call twice.
    if sink not in _sinks:
        _sinks.append(sink)


def _log_call(record: CallRecord) -> None:
    logger.info(
        "llm task=%s tier=%s provider=%s ok=%s fallback=%s %dms",
        record.task, record.tier, record.provider, record.success,
        record.fell_back, record.latency_ms,
    )
    for sink in _sinks:
        try:
            sink(record)
        except Exception:  # a broken logger must never break a student's turn
            logger.exception("llm call-log sink failed")


# --------------------------------------------------------------------------
# Clients (lazy — importing this module must not require keys)
# --------------------------------------------------------------------------
@dataclass
class _Clients:
    mistral: Any = None
    groq: Any = None
    built: bool = field(default=False)


_clients = _Clients()


def _ensure_clients() -> None:
    if _clients.built:
        return
    if settings.MISTRAL_API_KEY and Mistral is not None:
        _clients.mistral = Mistral(api_key=settings.MISTRAL_API_KEY)
    if settings.GROQ_API_KEY and Groq is not None:
        _clients.groq = Groq(api_key=settings.GROQ_API_KEY)
    _clients.built = True


def reset_clients() -> None:
    """Tests and key rotation."""
    _clients.mistral = None
    _clients.groq = None
    _clients.built = False


# --------------------------------------------------------------------------
# Retryability — duck-typed so an SDK version bump doesn't silently turn
# failover off.
# --------------------------------------------------------------------------
_RETRYABLE_NAME_HINTS = (
    "ratelimit", "timeout", "connection", "apistatus", "serviceunavailable",
    "internalserver", "overloaded", "sdkerror", "httperror",
)


def _status_code(exc: Exception) -> int | None:
    for attr in ("status_code", "http_status", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def is_retryable(exc: Exception) -> bool:
    """True for 429 / 408 / 5xx / timeout / connection failures only."""
    status = _status_code(exc)
    if status is not None:
        return status in (408, 409, 425, 429) or status >= 500
    name = type(exc).__name__.lower()
    return any(hint in name for hint in _RETRYABLE_NAME_HINTS)


# --------------------------------------------------------------------------
# JSON parsing
# --------------------------------------------------------------------------
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def _parse_json(text: str, provider: str) -> dict:
    if text is None:
        raise MalformedJSONError(provider, "")
    if not isinstance(text, str):  # some SDKs return content chunks
        text = "".join(
            getattr(part, "text", "") or "" for part in text
        ) or str(text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        candidate = text.strip()
        match = _FENCE.match(candidate)
        if match:
            candidate = match.group(1)
        else:
            # last resort: the outermost {...} in the response
            start, end = candidate.find("{"), candidate.rfind("}")
            candidate = candidate[start : end + 1] if start != -1 and end > start else candidate
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise MalformedJSONError(provider, text) from exc
    if not isinstance(parsed, dict):
        raise MalformedJSONError(provider, text)
    return parsed


# --------------------------------------------------------------------------
# Provider calls
# --------------------------------------------------------------------------
def _model_for(tier: str, provider: str) -> str:
    if provider == "mistral":
        return settings.DIALOGUE_MISTRAL_MODEL if tier == DIALOGUE else settings.MISTRAL_MODEL
    return settings.DIALOGUE_GROQ_MODEL if tier == DIALOGUE else settings.GROQ_FALLBACK_MODEL


def _call_mistral(messages: list[dict], model: str) -> str:
    resp = _clients.mistral.chat.complete(
        model=model,
        messages=messages,
        response_format={"type": "json_object"},
        timeout_ms=settings.LLM_TIMEOUT_SECONDS * 1000,
    )
    return resp.choices[0].message.content


def _call_groq(messages: list[dict], model: str) -> str:
    resp = _clients.groq.chat.completions.create(
        model=model,
        messages=messages,
        response_format={"type": "json_object"},
        timeout=settings.LLM_TIMEOUT_SECONDS,
    )
    return resp.choices[0].message.content


_PROVIDER_CALLS = {"mistral": _call_mistral, "groq": _call_groq}


def _available(provider: str) -> bool:
    _ensure_clients()
    return getattr(_clients, provider) is not None


def _provider_order() -> list[str]:
    """Primary first. MUSE_PRIMARY=groq flips them for a demo on a paid key."""
    primary = settings.PRIMARY_PROVIDER if settings.PRIMARY_PROVIDER in _PROVIDER_CALLS else "mistral"
    return [primary] + [p for p in ("mistral", "groq") if p != primary]


# --------------------------------------------------------------------------
# The one entry point
# --------------------------------------------------------------------------
def generate_structured(
    system_prompt: str,
    user_payload: str,
    schema_hint: str,
    max_retries: int = settings.MISTRAL_MAX_RETRIES,
    *,
    task: str = "unspecified",
    tier: str = CLASSIFICATION,
    temperature: float | None = None,
) -> dict:
    """Structured JSON from the primary provider, falling back to the secondary
    on rate-limit / 5xx / timeout. `schema_hint` is appended to the system
    prompt so both providers are asked for the same shape.
    """
    if settings.USE_FAKE_LLM or not (settings.MISTRAL_API_KEY or settings.GROQ_API_KEY):
        from app.llm.fake import fake_generate

        started = time.perf_counter()
        result = fake_generate(task, system_prompt, user_payload)
        _log_call(CallRecord(
            task=task, tier=tier, provider="fake", model="offline-stub",
            latency_ms=int((time.perf_counter() - started) * 1000), success=True,
        ))
        return result

    messages = [
        {
            "role": "system",
            "content": (
                f"{system_prompt}\n\nOutput schema:\n{schema_hint}\n"
                "Respond with ONLY valid JSON, no prose, no markdown fences."
            ),
        },
        {"role": "user", "content": user_payload},
    ]

    order = [p for p in _provider_order() if _available(p)]
    if not order:
        raise NoProviderConfiguredError(
            "No provider configured. Set MISTRAL_API_KEY and/or GROQ_API_KEY, "
            "or USE_FAKE_LLM=1 to run the offline stub."
        )

    last_error: Exception | None = None

    for index, provider in enumerate(order):
        is_fallback = index > 0
        # Only the primary gets retried — the fallback is already the retry.
        attempts_allowed = max(1, max_retries) if not is_fallback else 1

        for attempt in range(attempts_allowed):
            started = time.perf_counter()
            model = _model_for(tier, provider)
            try:
                raw = _PROVIDER_CALLS[provider](messages, model)
                parsed = _parse_json(raw, provider)
            except MalformedJSONError as exc:
                # Answered, but broke the JSON contract. Log loudly and raise —
                # failing over here would mask a prompt regression.
                _log_call(CallRecord(
                    task=task, tier=tier, provider=provider, model=model,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    success=False, fell_back=is_fallback, attempts=attempt + 1,
                    error_type="MalformedJSONError", error_detail=exc.raw[:2000],
                ))
                raise
            except Exception as exc:  # noqa: BLE001 - classified below
                latency = int((time.perf_counter() - started) * 1000)
                retryable = is_retryable(exc)
                _log_call(CallRecord(
                    task=task, tier=tier, provider=provider, model=model,
                    latency_ms=latency, success=False, fell_back=is_fallback,
                    attempts=attempt + 1, error_type=type(exc).__name__,
                    error_detail=str(exc)[:2000],
                ))
                last_error = exc
                if not retryable:
                    raise
                if attempt + 1 < attempts_allowed:
                    time.sleep(settings.MISTRAL_BACKOFF_SECONDS * (attempt + 1))
                    continue
                break  # move to the next provider
            else:
                _log_call(CallRecord(
                    task=task, tier=tier, provider=provider, model=model,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    success=True, fell_back=is_fallback, attempts=attempt + 1,
                ))
                return parsed

    raise ProviderExhaustedError(
        f"All providers ({', '.join(order)}) are rate-limited or down. "
        f"Last error: {type(last_error).__name__ if last_error else 'unknown'}: {last_error}"
    )
