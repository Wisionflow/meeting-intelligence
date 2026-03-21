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

    finally:
        await conn.close()

    print("\n✓ Database ready")


if __name__ == "__main__":
    asyncio.run(main())
