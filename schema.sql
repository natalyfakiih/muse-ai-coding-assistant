-- MUSE — PostgreSQL schema (Section 2).
-- SQLAlchemy creates these automatically; this file is the readable reference
-- and the thing to run if you'd rather manage migrations by hand.

CREATE TABLE IF NOT EXISTS students (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(200) NOT NULL,
    course_id   VARCHAR(100) NOT NULL,
    cohort      VARCHAR(100)
);
CREATE INDEX IF NOT EXISTS idx_students_course ON students (course_id);

CREATE TABLE IF NOT EXISTS assignments (
    id            SERIAL PRIMARY KEY,
    course_id     VARCHAR(100) NOT NULL,
    prompt_text   TEXT NOT NULL,
    -- {"entry_point": "fib",
    --  "items": [{"id": "empty_input", "text": "...", "priority": 4}, ...]}
    -- entry_point is the function the Reflection Engine calls when it runs the
    -- behavioral diff; items are what the Specification Gate checks against.
    rubric        JSONB NOT NULL DEFAULT '{}'::jsonb,
    starter_code  TEXT
);
CREATE INDEX IF NOT EXISTS idx_assignments_course ON assignments (course_id);

CREATE TABLE IF NOT EXISTS specifications (
    id              SERIAL PRIMARY KEY,
    assignment_id   INTEGER NOT NULL REFERENCES assignments (id),
    student_id      INTEGER NOT NULL REFERENCES students (id),
    version         INTEGER NOT NULL DEFAULT 1,
    -- {"goal", "inputs", "outputs", "edge_cases", "examples",
    --  "explicit_exclusions"} — the structured shape from Section 2.2
    content         JSONB NOT NULL DEFAULT '{}'::jsonb,
    status          VARCHAR(20) NOT NULL DEFAULT 'draft',   -- draft | locked
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- gate bookkeeping: asks per rubric item, and gaps the student confirmed
    gap_ask_counts  JSONB NOT NULL DEFAULT '{}'::jsonb,
    confirmed_gaps  JSONB NOT NULL DEFAULT '[]'::jsonb,
    submitted_at    TIMESTAMPTZ,
    UNIQUE (assignment_id, student_id, version)
);

CREATE TABLE IF NOT EXISTS code_snapshots (
    id                SERIAL PRIMARY KEY,
    specification_id  INTEGER NOT NULL REFERENCES specifications (id) ON DELETE CASCADE,
    content           TEXT NOT NULL DEFAULT '',
    timestamp         TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_final          BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_snapshots_spec ON code_snapshots (specification_id);

CREATE TABLE IF NOT EXISTS interactions (
    id                SERIAL PRIMARY KEY,
    specification_id  INTEGER NOT NULL REFERENCES specifications (id) ON DELETE CASCADE,
    student_id        INTEGER NOT NULL REFERENCES students (id),
    timestamp         TIMESTAMPTZ NOT NULL DEFAULT now(),
    student_message   TEXT NOT NULL,
    classified_type   VARCHAR(30),          -- factual | problem_solving
    ai_response       TEXT NOT NULL DEFAULT '',
    -- answer | counter_question | hint  ('hint' = escape hatch, assist cost logged)
    response_mode     VARCHAR(30) NOT NULL DEFAULT 'answer',
    confidence        DOUBLE PRECISION,
    reasoning         TEXT,
    assist_cost       INTEGER NOT NULL DEFAULT 0,
    thread_round      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_interactions_spec ON interactions (specification_id);

CREATE TABLE IF NOT EXISTS diff_results (
    id                   SERIAL PRIMARY KEY,
    specification_id     INTEGER NOT NULL REFERENCES specifications (id) ON DELETE CASCADE,
    code_snapshot_id     INTEGER REFERENCES code_snapshots (id),
    category             VARCHAR(20) NOT NULL,   -- drift | revision | bug
    description          TEXT NOT NULL,
    student_explanation  TEXT,
    instructor_flag      BOOLEAN NOT NULL DEFAULT FALSE,
    spec_item_key        VARCHAR(200),
    evidence             TEXT,
    test_detail          JSONB,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_diff_spec ON diff_results (specification_id);

-- Operational: which provider served each call, how long it took, did it fall back.
CREATE TABLE IF NOT EXISTS llm_calls (
    id            SERIAL PRIMARY KEY,
    timestamp     TIMESTAMPTZ NOT NULL DEFAULT now(),
    task          VARCHAR(60) NOT NULL,
    tier          VARCHAR(20) NOT NULL DEFAULT 'classification',
    provider      VARCHAR(20) NOT NULL,          -- mistral | groq | fake
    model         VARCHAR(100),
    latency_ms    INTEGER NOT NULL DEFAULT 0,
    success       BOOLEAN NOT NULL DEFAULT TRUE,
    fell_back     BOOLEAN NOT NULL DEFAULT FALSE,
    error_type    VARCHAR(80),
    error_detail  TEXT,
    attempts      INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_ts ON llm_calls (timestamp);
CREATE INDEX IF NOT EXISTS idx_llm_calls_task ON llm_calls (task);

-- Dependency-profile visibility is a consent question, so it lives here rather
-- than as a toggle in the dashboard UI (Section 3.5).
CREATE TABLE IF NOT EXISTS course_settings (
    course_id                      VARCHAR(100) PRIMARY KEY,
    dependency_profile_visibility  VARCHAR(20) NOT NULL DEFAULT 'student_only'
);
