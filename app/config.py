"""Central configuration. Everything tunable lives here or in .env."""
from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


class Settings:
    # --- database -------------------------------------------------------
    # Postgres in production:
    #   postgresql+psycopg://muse:muse@localhost:5432/muse
    # SQLite is the zero-setup default so the project runs out of the box.
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./muse.db")

    # --- providers ------------------------------------------------------
    MISTRAL_API_KEY: str | None = os.getenv("MISTRAL_API_KEY") or None
    GROQ_API_KEY: str | None = os.getenv("GROQ_API_KEY") or None

    MISTRAL_MODEL: str = os.getenv("MISTRAL_MODEL", "mistral-small-latest")
    GROQ_FALLBACK_MODEL: str = os.getenv("GROQ_FALLBACK_MODEL", "openai/gpt-oss-20b")

    # The dialogue tier is the quality-sensitive one (Socratic questions,
    # clarifying questions, narrative summaries). Point it at a stronger
    # model — or a paid key — without touching any module code.
    DIALOGUE_MISTRAL_MODEL: str = os.getenv("DIALOGUE_MISTRAL_MODEL", MISTRAL_MODEL)
    DIALOGUE_GROQ_MODEL: str = os.getenv("DIALOGUE_GROQ_MODEL", GROQ_FALLBACK_MODEL)

    MISTRAL_MAX_RETRIES: int = _int("MISTRAL_MAX_RETRIES", 2)
    MISTRAL_BACKOFF_SECONDS: float = float(os.getenv("MISTRAL_BACKOFF_SECONDS", "1.5"))
    LLM_TIMEOUT_SECONDS: int = _int("LLM_TIMEOUT_SECONDS", 30)

    # Flip the primary provider for a demo (e.g. a paid Groq key with no
    # RPM ceiling) without editing code: MUSE_PRIMARY=groq
    PRIMARY_PROVIDER: str = os.getenv("MUSE_PRIMARY", "mistral").strip().lower()

    # With no keys configured MUSE runs on a deterministic offline stub so
    # the whole flow is demoable and testable without burning quota.
    USE_FAKE_LLM: bool = _bool("USE_FAKE_LLM", False)

    # --- pedagogy knobs (Section 8 calls these tunable, not settled) ----
    GATE_MAX_ASKS_PER_GAP: int = _int("GATE_MAX_ASKS_PER_GAP", 2)
    TUTOR_ESCAPE_HATCH_ROUNDS: int = _int("TUTOR_ESCAPE_HATCH_ROUNDS", 3)

    # --- code execution sandbox ----------------------------------------
    SANDBOX_TIMEOUT_SECONDS: int = _int("SANDBOX_TIMEOUT_SECONDS", 5)
    SANDBOX_MEMORY_MB: int = _int("SANDBOX_MEMORY_MB", 256)
    SANDBOX_MAX_OUTPUT_BYTES: int = _int("SANDBOX_MAX_OUTPUT_BYTES", 64_000)

    @property
    def llm_enabled(self) -> bool:
        return not self.USE_FAKE_LLM and bool(self.MISTRAL_API_KEY or self.GROQ_API_KEY)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
