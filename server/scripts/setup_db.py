"""Create Meeting Intelligence tables in PostgreSQL.

Run once before first start:
    python scripts/setup_db.py
"""

import asyncio
import sys

sys.path.insert(0, ".")

import asyncpg
from src.config import POSTGRES_DSN


SCHEMA = """
-- Meetings
CREATE TABLE IF NOT EXISTS mi_meetings (
    id              SERIAL PRIMARY KEY,
    filename        TEXT NOT NULL,
    duration_sec    REAL DEFAULT 0,
    language        TEXT DEFAULT '',
    transcript_text TEXT DEFAULT '',
    transcript_formatted TEXT DEFAULT '',
    analysis_text   TEXT DEFAULT '',
    segments        JSONB DEFAULT '[]',
    status          TEXT DEFAULT 'processing',  -- processing | completed | error
    error_message   TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mi_meetings_status ON mi_meetings(status);
CREATE INDEX IF NOT EXISTS idx_mi_meetings_created ON mi_meetings(created_at DESC);

-- Profiles (communication guides)
CREATE TABLE IF NOT EXISTS mi_profiles (
    id              TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    role            TEXT DEFAULT '',
    department      TEXT DEFAULT '',
    guide           TEXT DEFAULT '',
    profile_data    TEXT DEFAULT '',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Access control (who can see which profiles)
CREATE TABLE IF NOT EXISTS mi_profile_access (
    user_id         TEXT NOT NULL,
    profile_id      TEXT NOT NULL,
    access_level    TEXT DEFAULT 'guide',
    PRIMARY KEY (user_id, profile_id)
);

-- Communication history (every generated message)
CREATE TABLE IF NOT EXISTS mi_communications (
    id              SERIAL PRIMARY KEY,
    user_id         TEXT DEFAULT '',
    profile_id      TEXT NOT NULL,
    profile_name    TEXT DEFAULT '',
    context         TEXT DEFAULT '',
    context_type    TEXT DEFAULT 'text',     -- text | audio
    audio_filename  TEXT,
    transcript      TEXT,
    task            TEXT DEFAULT '',
    goal            TEXT DEFAULT 'respond',
    msg_type        TEXT DEFAULT 'email',
    generated_message TEXT NOT NULL,
    notes           TEXT,
    tokens_input    INT DEFAULT 0,
    tokens_output   INT DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mi_comm_user ON mi_communications(user_id);
CREATE INDEX IF NOT EXISTS idx_mi_comm_profile ON mi_communications(profile_id);
CREATE INDEX IF NOT EXISTS idx_mi_comm_created ON mi_communications(created_at DESC);

-- User Memory (CI-MEMORY: contextual memory for VisionFlow users)
CREATE TABLE IF NOT EXISTS mi_user_memory (
    id              SERIAL PRIMARY KEY,
    user_id         TEXT NOT NULL,
    memory_type     TEXT NOT NULL,       -- context | pattern | preference | decision
    content         TEXT NOT NULL,        -- the actual memory content
    entities        JSONB DEFAULT '[]',   -- extracted entities: people, tasks, decisions
    source          TEXT DEFAULT '',      -- chat | meeting | upload | system
    source_id       TEXT DEFAULT '',      -- meeting_id or chat message ref
    relevance       REAL DEFAULT 1.0,     -- decays over time (temporal relevance)
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    expires_at      TIMESTAMPTZ           -- NULL = permanent, else auto-cleanup
);

CREATE INDEX IF NOT EXISTS idx_mi_memory_user ON mi_user_memory(user_id);
CREATE INDEX IF NOT EXISTS idx_mi_memory_type ON mi_user_memory(user_id, memory_type);
CREATE INDEX IF NOT EXISTS idx_mi_memory_created ON mi_user_memory(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_mi_memory_entities ON mi_user_memory USING gin(entities);

-- Session backup (resilience: recoverable after crashes)
CREATE TABLE IF NOT EXISTS mi_user_sessions (
    id              SERIAL PRIMARY KEY,
    user_id         TEXT NOT NULL,
    session_data    JSONB NOT NULL,       -- full chat history snapshot
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mi_sessions_user ON mi_user_sessions(user_id, created_at DESC);
"""


async def main():
    print(f"Connecting to: {POSTGRES_DSN.split('@')[1]}...")
    conn = await asyncpg.connect(POSTGRES_DSN)

    try:
        await conn.execute(SCHEMA)
        print("✓ Tables created successfully")

        count = await conn.fetchval("SELECT COUNT(*) FROM mi_meetings")
        print(f"  mi_meetings: {count} records")

        count_p = await conn.fetchval("SELECT COUNT(*) FROM mi_profiles")
        print(f"  mi_profiles: {count_p} records")

        count_a = await conn.fetchval("SELECT COUNT(*) FROM mi_profile_access")
        print(f"  mi_profile_access: {count_a} records")

        count_c = await conn.fetchval("SELECT COUNT(*) FROM mi_communications")
        print(f"  mi_communications: {count_c} records")

        count_m = await conn.fetchval("SELECT COUNT(*) FROM mi_user_memory")
        print(f"  mi_user_memory: {count_m} records")

        count_s = await conn.fetchval("SELECT COUNT(*) FROM mi_user_sessions")
        print(f"  mi_user_sessions: {count_s} records")

    finally:
        await conn.close()

    print("\n✓ Database ready")


if __name__ == "__main__":
    asyncio.run(main())
