"""The failover policy is the load-bearing part of this system under classroom
load, so it gets tested directly rather than through the API."""
from __future__ import annotations

import pytest

from app.llm import client as llm


class RateLimited(Exception):
    status_code = 429


class ServerError(Exception):
    status_code = 503


class BadRequest(Exception):
    status_code = 400


class Timeout(Exception):
    pass  # name-based detection: "timeout"


@pytest.fixture(autouse=True)
def live_providers(monkeypatch):
    """Pretend both providers are configured and instantaneous."""
    monkeypatch.setattr(llm.settings, "USE_FAKE_LLM", False)
    monkeypatch.setattr(llm.settings, "MISTRAL_API_KEY", "test-mistral")
    monkeypatch.setattr(llm.settings, "GROQ_API_KEY", "test-groq")
    monkeypatch.setattr(llm.settings, "MISTRAL_BACKOFF_SECONDS", 0.0)
    monkeypatch.setattr(llm, "_available", lambda provider: True)
    llm._sinks.clear()
    yield
    llm._sinks.clear()


def wire(monkeypatch, mistral, groq):
    monkeypatch.setattr(llm, "_PROVIDER_CALLS", {"mistral": mistral, "groq": groq})


def test_mistral_serves_when_healthy(monkeypatch):
    calls = []
    wire(monkeypatch,
         lambda m, model: (calls.append("mistral"), '{"type": "factual"}')[1],
         lambda m, model: (calls.append("groq"), '{"type": "groq"}')[1])
    assert llm.generate_structured("s", "u", "{}")["type"] == "factual"
    assert calls == ["mistral"]


def test_rate_limit_falls_straight_over_to_groq(monkeypatch):
    """A 429 gets no retry. Measured against a real free-tier key, retrying it
    cost a failed round trip plus a 1.5s sleep on every single call, and the
    second attempt hit the same cap every time."""
    calls = []

    def mistral(messages, model):
        calls.append("mistral")
        raise RateLimited("429 too many requests")

    def groq(messages, model):
        calls.append("groq")
        return '{"type": "factual", "via": "groq"}'

    wire(monkeypatch, mistral, groq)
    result = llm.generate_structured("s", "u", "{}", max_retries=2)
    assert result["via"] == "groq"
    assert calls == ["mistral", "groq"]


@pytest.mark.parametrize("error", [RateLimited, ServerError, Timeout])
def test_all_retryable_failure_modes_fail_over(monkeypatch, error):
    def mistral(messages, model):
        raise error("boom")

    wire(monkeypatch, mistral, lambda m, model: '{"ok": true}')
    assert llm.generate_structured("s", "u", "{}")["ok"] is True


def test_non_retryable_error_raises_without_falling_over(monkeypatch):
    """A 400 is a bug in our request. Retrying it on Groq just hides it."""
    groq_called = []

    def mistral(messages, model):
        raise BadRequest("400 bad request")

    wire(monkeypatch, mistral, lambda m, model: groq_called.append(1) or '{"ok": true}')
    with pytest.raises(BadRequest):
        llm.generate_structured("s", "u", "{}")
    assert groq_called == []


def test_malformed_json_raises_and_does_not_fail_over(monkeypatch):
    """A model that stopped honouring the JSON contract is a prompt bug, not a
    capacity problem — it must surface, not get silently swallowed by Groq."""
    groq_called = []
    wire(monkeypatch,
         lambda m, model: "Sure! Here's the answer: the loop is off by one.",
         lambda m, model: groq_called.append(1) or '{"ok": true}')
    with pytest.raises(llm.MalformedJSONError) as raised:
        llm.generate_structured("s", "u", "{}")
    assert raised.value.provider == "mistral"
    assert groq_called == []


def test_both_providers_down_raises_provider_exhausted(monkeypatch):
    def down(messages, model):
        raise ServerError("503")

    wire(monkeypatch, down, down)
    with pytest.raises(llm.ProviderExhaustedError):
        llm.generate_structured("s", "u", "{}")


def test_markdown_fences_are_stripped(monkeypatch):
    wire(monkeypatch, lambda m, model: '```json\n{"type": "factual"}\n```', lambda m, model: "{}")
    assert llm.generate_structured("s", "u", "{}")["type"] == "factual"


def test_prose_around_json_is_recovered(monkeypatch):
    wire(monkeypatch, lambda m, model: 'Here you go: {"type": "factual"} — hope that helps',
         lambda m, model: "{}")
    assert llm.generate_structured("s", "u", "{}")["type"] == "factual"


def test_every_call_is_logged_with_provider_and_fallback_flag(monkeypatch):
    records = []
    llm.register_call_logger(records.append)

    def mistral(messages, model):
        raise RateLimited("429")

    wire(monkeypatch, mistral, lambda m, model: '{"ok": true}')
    llm.generate_structured("s", "u", "{}", max_retries=1, task="classify_question")

    assert [r.provider for r in records] == ["mistral", "groq"]
    assert records[0].success is False and records[0].error_type == "RateLimited"
    assert records[1].success is True and records[1].fell_back is True
    assert records[1].task == "classify_question"


def test_primary_can_be_flipped_to_groq_for_a_demo(monkeypatch):
    """Section 4: point the classification tier at a paid key for the demo so a
    live audience never sees a 429, then flip it back."""
    monkeypatch.setattr(llm.settings, "PRIMARY_PROVIDER", "groq")
    calls = []
    wire(monkeypatch,
         lambda m, model: calls.append("mistral") or "{}",
         lambda m, model: calls.append("groq") or '{"ok": true}')
    llm.generate_structured("s", "u", "{}")
    assert calls == ["groq"]


def test_dialogue_tier_can_use_a_different_model(monkeypatch):
    monkeypatch.setattr(llm.settings, "DIALOGUE_MISTRAL_MODEL", "mistral-large-latest")
    seen = []
    wire(monkeypatch, lambda m, model: seen.append(model) or '{"ok": true}', lambda m, model: "{}")
    llm.generate_structured("s", "u", "{}", tier=llm.CLASSIFICATION)
    llm.generate_structured("s", "u", "{}", tier=llm.DIALOGUE)
    assert seen == [llm.settings.MISTRAL_MODEL, "mistral-large-latest"]


def test_retryability_classification():
    assert llm.is_retryable(RateLimited("x")) is True
    assert llm.is_retryable(ServerError("x")) is True
    assert llm.is_retryable(Timeout("x")) is True
    assert llm.is_retryable(BadRequest("x")) is False
    assert llm.is_retryable(ValueError("x")) is False


# --------------------------------------------------------------------------
# Rate limiting — measured cause of a 2.9x slowdown against a free-tier key
# --------------------------------------------------------------------------
class SDKError(Exception):
    """Shape mistralai actually raises: the 429 is only in the message."""
    def __init__(self):
        super().__init__('API error occurred: Status 429. Body: '
                         '{"message":"Rate limit exceeded","type":"rate_limited"}')


@pytest.fixture(autouse=True)
def _clean_breakers():
    llm.reset_breakers()
    yield
    llm.reset_breakers()


def test_a_429_buried_in_the_message_is_still_a_rate_limit():
    assert llm.is_rate_limit(SDKError()) is True
    assert llm.is_rate_limit(RateLimited("429")) is True
    assert llm.is_rate_limit(ServerError("503")) is False


def test_a_rate_limited_primary_fails_over_immediately(monkeypatch):
    """No second attempt and no backoff: the cap will say the same thing 1.5s
    later, and that wait was 1.5s of every single request."""
    calls = []
    slept = []
    monkeypatch.setattr(llm.time, "sleep", lambda s: slept.append(s))

    def mistral(messages, model):
        calls.append("mistral")
        raise SDKError()

    wire(monkeypatch, mistral, lambda m, model: calls.append("groq") or '{"ok": true}')
    assert llm.generate_structured("s", "u", "{}", max_retries=2)["ok"] is True

    assert calls == ["mistral", "groq"], "one attempt, then straight to the fallback"
    assert slept == [], "no backoff on a rate limit"


def test_a_5xx_still_gets_its_retry(monkeypatch):
    """The distinction matters: a 503 may well succeed a moment later."""
    calls = []
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)

    def mistral(messages, model):
        calls.append("mistral")
        raise ServerError("503")

    wire(monkeypatch, mistral, lambda m, model: calls.append("groq") or '{"ok": true}')
    llm.generate_structured("s", "u", "{}", max_retries=2)
    assert calls == ["mistral", "mistral", "groq"]


def test_repeated_rate_limits_open_the_breaker_and_skip_the_primary(monkeypatch):
    calls = []
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)

    def mistral(messages, model):
        calls.append("mistral")
        raise SDKError()

    wire(monkeypatch, mistral, lambda m, model: calls.append("groq") or '{"ok": true}')

    for _ in range(4):
        llm.generate_structured("s", "u", "{}")

    # Two strikes open the breaker; the remaining calls skip Mistral entirely.
    assert calls.count("mistral") == 2, f"primary should stop being tried: {calls}"
    assert calls.count("groq") == 4
    assert llm.breaker_is_open("mistral") is True


def test_a_skipped_primary_is_still_reported_as_a_fallback(monkeypatch):
    """The ops view has to keep telling the truth about what served the call."""
    records = []
    llm.register_call_logger(records.append)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    wire(monkeypatch,
         lambda m, model: (_ for _ in ()).throw(SDKError()),
         lambda m, model: '{"ok": true}')

    for _ in range(3):
        llm.generate_structured("s", "u", "{}")

    served = [r for r in records if r.success]
    assert all(r.provider == "groq" and r.fell_back for r in served)


def test_the_breaker_reopens_the_primary_after_the_cooldown(monkeypatch):
    """A cooldown, not a write-off — the cap resets and so should we."""
    monkeypatch.setattr(llm.settings, "BREAKER_COOLDOWN_SECONDS", 0)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    calls = []

    def mistral(messages, model):
        calls.append("mistral")
        raise SDKError()

    wire(monkeypatch, mistral, lambda m, model: '{"ok": true}')
    for _ in range(3):
        llm.generate_structured("s", "u", "{}")

    assert calls.count("mistral") == 3, "a zero cooldown means always retry"


def test_a_successful_call_clears_the_strikes(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    state = {"fail": True}

    def mistral(messages, model):
        if state["fail"]:
            raise SDKError()
        return '{"ok": true}'

    wire(monkeypatch, mistral, lambda m, model: '{"ok": true}')
    llm.generate_structured("s", "u", "{}")
    state["fail"] = False
    llm.generate_structured("s", "u", "{}")

    assert llm.breaker_is_open("mistral") is False
    assert llm.breaker_status()["mistral"]["consecutive_rate_limits"] == 0


# --------------------------------------------------------------------------
# Expired and rejected keys
#
# A 401 is not an outage and not a bug in our request: it is a key that needs
# replacing. It used to escape generate_structured() raw, which both skipped a
# healthy fallback and surfaced a provider's own exception to the student as a
# 500 with a traceback.
# --------------------------------------------------------------------------
class Unauthorized(Exception):
    status_code = 401


def test_a_dead_key_on_the_primary_falls_over_to_the_healthy_one(monkeypatch):
    """One expired key must not take the request down while the other provider
    is fine."""
    wire(monkeypatch,
         lambda m, model: (_ for _ in ()).throw(Unauthorized("Invalid API Key")),
         lambda m, model: '{"type": "served-by-groq"}')

    assert llm.generate_structured("s", "u", "{}")["type"] == "served-by-groq"


def test_when_every_key_is_rejected_the_error_names_the_fix(monkeypatch):
    """"All providers are rate-limited or down" sends the reader hunting for an
    outage that is not happening. The actual fix is a new key."""
    def rejected(messages, model):
        raise Unauthorized("Error code: 401 - invalid_api_key: expired_api_key")

    wire(monkeypatch, rejected, rejected)

    with pytest.raises(llm.ProviderExhaustedError) as raised:
        llm.generate_structured("s", "u", "{}")

    message = str(raised.value).lower()
    assert "invalid or expired" in message
    assert "mistral" in message and "groq" in message
    assert ".env" in message          # where to put the new one
    assert "restart" in message       # --reload does not reload .env
    assert "use_fake_llm" in message  # how to keep working without a key


def test_a_rejected_key_is_logged_as_an_auth_failure_not_a_rate_limit(monkeypatch):
    """The ops log has to separate "we are over quota" from "this key is dead";
    they look identical in a latency chart and have nothing in common."""
    seen = []
    llm.register_call_logger(seen.append)
    try:
        wire(monkeypatch,
             lambda m, model: (_ for _ in ()).throw(Unauthorized("Invalid API Key")),
             lambda m, model: '{"ok": true}')
        llm.generate_structured("s", "u", "{}")
    finally:
        llm._sinks.clear()

    failures = [r for r in seen if not r.success]
    assert failures and failures[0].error_type == "AuthFailed"


@pytest.mark.parametrize("exc", [
    Unauthorized("401"),
    Exception("Error code: 401 - {'error': {'code': 'expired_api_key'}}"),
    Exception("AuthenticationError: Invalid API Key"),
])
def test_the_shapes_a_rejected_key_arrives_in(exc):
    assert llm.is_auth_error(exc) is True


@pytest.mark.parametrize("exc", [RateLimited("429"), ServerError("503"), BadRequest("400")])
def test_other_failures_are_not_mistaken_for_auth(exc):
    assert llm.is_auth_error(exc) is False
