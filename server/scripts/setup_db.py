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
"""


async def main():
    print(f"Connecting to: {POSTGRES_DSN.split('@')[1]}...")
    conn = await asyncpg.connect(POSTGRES_DSN)

    try:
        await conn.execute(SCHEMA)
        print("✓ Tables created successfully")

        count = await conn.fetchval("SELECT COUNT(*) FROM mi_meetings")
        print(f"  mi_meetings: {count} records")

    finally:
        await conn.close()

    print("\n✓ Database ready")


if __name__ == "__main__":
    asyncio.run(main())
