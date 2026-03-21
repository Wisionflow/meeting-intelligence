"""Profile storage and access control for VisionFlow.

Stores communication guides in PostgreSQL.
Provides limited access: only guide (how to communicate), NOT raw DISC/Big5 scores.
"""

import json
import logging

import asyncpg

from src.config import POSTGRES_DSN

log = logging.getLogger(__name__)


async def _get_pool():
    from src.storage import get_pool
    return await get_pool()


# ─── Schema ──────────────────────────────────────────────────────────────────

PROFILES_SCHEMA = """
CREATE TABLE IF NOT EXISTS mi_profiles (
    id              TEXT PRIMARY KEY,          -- person slug (e.g. melnikov-anatolij-igorevich)
    display_name    TEXT NOT NULL,             -- Мельников Анатолий Игоревич
    role            TEXT DEFAULT '',           -- Коммерческий директор
    department      TEXT DEFAULT '',           -- ОП Москва
    guide           TEXT DEFAULT '',           -- communication-guide.md content (VISIBLE to users)
    profile_data    TEXT DEFAULT '',           -- psychotype-profile.md (HIDDEN from regular users)
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS mi_profile_access (
    user_id         TEXT NOT NULL,             -- who has access (e.g. khrenova)
    profile_id      TEXT NOT NULL,             -- which profile they can see
    access_level    TEXT DEFAULT 'guide',      -- guide | full
    PRIMARY KEY (user_id, profile_id)
);
"""


async def setup_tables():
    pool = await _get_pool()
    await pool.execute(PROFILES_SCHEMA)
    log.info("Profile tables created/verified")


# ─── Import profiles ────────────────────────────────────────────────────────

async def upsert_profile(
    person_id: str,
    display_name: str,
    role: str = "",
    department: str = "",
    guide: str = "",
    profile_data: str = "",
):
    """Insert or update a person's profile."""
    pool = await _get_pool()
    await pool.execute(
        """
        INSERT INTO mi_profiles (id, display_name, role, department, guide, profile_data, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, NOW())
        ON CONFLICT (id) DO UPDATE SET
            display_name = $2, role = $3, department = $4,
            guide = $5, profile_data = $6, updated_at = NOW()
        """,
        person_id, display_name, role, department, guide, profile_data,
    )
    log.info("Profile upserted: %s (%s)", display_name, person_id)


async def grant_access(user_id: str, profile_id: str, access_level: str = "guide"):
    """Grant a user access to a profile."""
    pool = await _get_pool()
    await pool.execute(
        """
        INSERT INTO mi_profile_access (user_id, profile_id, access_level)
        VALUES ($1, $2, $3)
        ON CONFLICT (user_id, profile_id) DO UPDATE SET access_level = $3
        """,
        user_id, profile_id, access_level,
    )
    log.info("Access granted: %s → %s (%s)", user_id, profile_id, access_level)


# ─── Read profiles (with access control) ────────────────────────────────────

async def list_profiles(user_id: str | None = None) -> list[dict]:
    """List profiles visible to a user. If user_id is None, list all (admin)."""
    pool = await _get_pool()

    if user_id:
        rows = await pool.fetch(
            """
            SELECT p.id, p.display_name, p.role, p.department
            FROM mi_profiles p
            JOIN mi_profile_access a ON a.profile_id = p.id
            WHERE a.user_id = $1
            ORDER BY p.display_name
            """,
            user_id,
        )
    else:
        rows = await pool.fetch(
            "SELECT id, display_name, role, department FROM mi_profiles ORDER BY display_name"
        )

    return [dict(r) for r in rows]


async def get_guide(profile_id: str, user_id: str | None = None) -> dict | None:
    """Get communication guide for a person. Checks access if user_id provided."""
    pool = await _get_pool()

    if user_id:
        # Check access
        access = await pool.fetchrow(
            "SELECT access_level FROM mi_profile_access WHERE user_id = $1 AND profile_id = $2",
            user_id, profile_id,
        )
        if not access:
            return None  # no access

    row = await pool.fetchrow(
        "SELECT id, display_name, role, department, guide FROM mi_profiles WHERE id = $1",
        profile_id,
    )
    if not row:
        return None
    return dict(row)


async def get_full_profile(profile_id: str) -> dict | None:
    """Get full profile (admin only — includes profile_data with DISC/Big5)."""
    pool = await _get_pool()
    row = await pool.fetchrow("SELECT * FROM mi_profiles WHERE id = $1", profile_id)
    if not row:
        return None
    result = dict(row)
    if result.get("created_at"):
        result["created_at"] = result["created_at"].isoformat()
    if result.get("updated_at"):
        result["updated_at"] = result["updated_at"].isoformat()
    return result
