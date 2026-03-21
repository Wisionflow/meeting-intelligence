"""PostgreSQL storage for Meeting Intelligence.

Tables: mi_meetings, mi_tasks
"""

import json
import logging
from datetime import datetime

import asyncpg

from src.config import POSTGRES_DSN

log = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(POSTGRES_DSN, min_size=1, max_size=5)
        log.info("PostgreSQL pool created")
    return _pool


async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def save_meeting(
    filename: str,
    duration_sec: float,
    language: str,
    transcript_text: str,
    transcript_formatted: str,
    analysis_text: str,
    segments_json: list[dict],
) -> int:
    """Save meeting to database. Returns meeting ID."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO mi_meetings
            (filename, duration_sec, language, transcript_text, transcript_formatted,
             analysis_text, segments, status, created_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, 'completed', NOW())
        RETURNING id
        """,
        filename,
        duration_sec,
        language,
        transcript_text,
        transcript_formatted,
        analysis_text,
        json.dumps(segments_json, ensure_ascii=False),
    )
    meeting_id = row["id"]
    log.info("Saved meeting #%d: %s (%.0f sec)", meeting_id, filename, duration_sec)
    return meeting_id


async def get_meeting(meeting_id: int) -> dict | None:
    pool = await get_pool()
    row = await pool.fetchrow("SELECT * FROM mi_meetings WHERE id = $1", meeting_id)
    if not row:
        return None
    return dict(row)


async def list_meetings(limit: int = 50, offset: int = 0) -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT id, filename, duration_sec, language, status, created_at
        FROM mi_meetings
        ORDER BY created_at DESC
        LIMIT $1 OFFSET $2
        """,
        limit,
        offset,
    )
    return [dict(r) for r in rows]


async def count_meetings() -> int:
    pool = await get_pool()
    row = await pool.fetchrow("SELECT COUNT(*) as cnt FROM mi_meetings")
    return row["cnt"]


async def save_processing_status(filename: str, status: str, error: str | None = None) -> int:
    """Save a meeting record with processing/error status."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO mi_meetings
            (filename, duration_sec, language, transcript_text, transcript_formatted,
             analysis_text, segments, status, error_message, created_at)
        VALUES ($1, 0, '', '', '', '', '[]', $2, $3, NOW())
        RETURNING id
        """,
        filename,
        status,
        error,
    )
    return row["id"]


async def update_meeting_status(meeting_id: int, status: str, error: str | None = None):
    pool = await get_pool()
    await pool.execute(
        "UPDATE mi_meetings SET status = $1, error_message = $2 WHERE id = $3",
        status,
        error,
        meeting_id,
    )
