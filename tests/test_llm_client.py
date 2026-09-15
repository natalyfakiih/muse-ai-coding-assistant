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


def test_rate_limit_retries_then_falls_back_to_groq(monkeypatch):
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
    # two quick attempts at Mistral, then straight to Groq — not 30s of retries
    assert calls == ["mistral", "mistral", "groq"]


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
