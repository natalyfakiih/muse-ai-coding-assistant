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


def is_auth_error(exc: Exception) -> bool:
    """A bad, revoked or expired key. Retrying cannot help and the next request
    will fail identically, so this has to read differently from an outage: the
    fix is a new key, not waiting."""
    if _status_code(exc) in (401, 403):
        return True
    text = f"{type(exc).__name__} {exc}".lower()
    return any(hint in text for hint in (
        "invalid api key", "expired_api_key", "authenticationerror",
        "unauthorized", "invalid_api_key",
    ))


def is_rate_limit(exc: Exception) -> bool:
    """A 429 specifically, including SDKs that bury it in the message.

    Worth separating from the rest: a 5xx may well succeed a second later, but
    a provider saying "you are over your requests-per-minute cap" will say the
    same thing 1.5 seconds later. Retrying that is pure waiting.
    """
    if _status_code(exc) == 429:
        return True
    text = f"{type(exc).__name__} {exc}".lower()
    return "429" in text or "rate limit" in text or "rate_limited" in text


# --------------------------------------------------------------------------
# Circuit breaker
#
# Mistral's free tier has a single-digit RPM cap, so under classroom load it
# rate-limits nearly every call. Without this, EVERY request pays two failed
# round trips plus a backoff sleep before reaching Groq — measured at ~2.6s of
# pure overhead per call against a ~1.4s useful call. After a couple of 429s in
# a row the provider is skipped outright for a cooldown, then quietly retried.
# --------------------------------------------------------------------------
_breaker: dict[str, dict[str, float]] = {}


def _breaker_state(provider: str) -> dict[str, float]:
    return _breaker.setdefault(provider, {"open_until": 0.0, "strikes": 0.0})


def breaker_is_open(provider: str) -> bool:
    return time.monotonic() < _breaker_state(provider)["open_until"]


def _record_rate_limit(provider: str) -> None:
    state = _breaker_state(provider)
    state["strikes"] += 1
    if state["strikes"] >= settings.BREAKER_STRIKES:
        state["open_until"] = time.monotonic() + settings.BREAKER_COOLDOWN_SECONDS
        logger.warning(
            "%s rate-limited %d times in a row — skipping it for %ss",
            provider, int(state["strikes"]), settings.BREAKER_COOLDOWN_SECONDS,
        )


def _record_success(provider: str) -> None:
    _breaker[provider] = {"open_until": 0.0, "strikes": 0.0}


def reset_breakers() -> None:
    _breaker.clear()


def breaker_status() -> dict[str, dict]:
    now = time.monotonic()
    return {
        provider: {
            "open": now < state["open_until"],
            "seconds_left": max(0, round(state["open_until"] - now, 1)),
            "consecutive_rate_limits": int(state["strikes"]),
        }
        for provider, state in _breaker.items()
    }


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

    available = [p for p in _provider_order() if _available(p)]
    # Skip a provider we already know is rate-limited, unless it's all we have.
    order = [p for p in available if not breaker_is_open(p)] or available
    skipped_primary = bool(available) and order and order[0] != available[0]
    if not order:
        raise NoProviderConfiguredError(
            "No provider configured. Set MISTRAL_API_KEY and/or GROQ_API_KEY, "
            "or USE_FAKE_LLM=1 to run the offline stub."
        )

    last_error: Exception | None = None
    auth_failures: list[str] = []

    for index, provider in enumerate(order):
        # A provider reached only because the primary is in cooldown is still a
        # fallback, so the ops view keeps telling the truth about what served.
        is_fallback = index > 0 or skipped_primary
        # Only the primary gets retried — the fallback is already the retry.
        attempts_allowed = max(1, max_retries) if index == 0 else 1

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
                rate_limited = is_rate_limit(exc)
                retryable = is_retryable(exc)
                auth_failed = is_auth_error(exc)
                if auth_failed:
                    auth_failures.append(provider)
                _log_call(CallRecord(
                    task=task, tier=tier, provider=provider, model=model,
                    latency_ms=latency, success=False, fell_back=is_fallback,
                    attempts=attempt + 1,
                    error_type=("AuthFailed" if auth_failed else
                                "RateLimited" if rate_limited else type(exc).__name__),
                    error_detail=str(exc)[:2000],
                ))
                last_error = exc
                if auth_failed:
                    # One dead key must not take the request down while the
                    # other provider is healthy. Retrying this one is pointless,
                    # so stop here and let the fallback have it.
                    break
                if not retryable:
                    # Still raises. A 400 means WE sent a bad request, and
                    # trying the same request on the other provider only hides
                    # our own bug. Only the auth case above falls through, and
                    # it does so because a dead key says nothing about whether
                    # the request was well formed.
                    raise
                if rate_limited:
                    # Over quota. A second attempt 1.5s later hits the same cap,
                    # so go to the fallback now and stop trying this provider
                    # for a while — that overhead is otherwise paid per call.
                    _record_rate_limit(provider)
                    break
                if attempt + 1 < attempts_allowed:
                    time.sleep(settings.MISTRAL_BACKOFF_SECONDS * (attempt + 1))
                    continue
                break  # move to the next provider
            else:
                _record_success(provider)
                _log_call(CallRecord(
                    task=task, tier=tier, provider=provider, model=model,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    success=True, fell_back=is_fallback, attempts=attempt + 1,
                ))
                return parsed

    if auth_failures:
        # The single most common cause, and the one with a concrete fix. Saying
        # "rate-limited or down" here sends the reader looking for an outage
        # that is not happening.
        raise ProviderExhaustedError(
            f"The API key for {' and '.join(sorted(set(auth_failures)))} was "
            f"rejected as invalid or expired. Issue a new key, put it in .env, "
            f"then fully restart the server (--reload does not pick up .env). "
            f"To keep working without a key in the meantime, set USE_FAKE_LLM=1."
        )
    raise ProviderExhaustedError(
        f"All providers ({', '.join(order)}) are rate-limited or down. "
        f"Last error: {type(last_error).__name__ if last_error else 'unknown'}: {last_error}"
    )
