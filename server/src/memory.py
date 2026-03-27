"""User Memory — contextual memory for VisionFlow users.

Stores and retrieves user context between sessions:
  - context:    people discussed, decisions made, tasks assigned
  - pattern:    behavioral observations extracted from uploads
  - preference: communication style, frequently used features
  - decision:   system recommendations accepted/rejected (feedback loop)

Degraded mode: if DB unavailable, all functions return empty results
instead of crashing. Chat works without memory, just without context.

Usage:
    memories = await recall(user_id, query="Ермилов", limit=5)
    await remember(user_id, "context", "Обсуждали конфликт Ермилова и Мельникова",
                   entities=["ермилов", "мельников"], source="chat")
"""

import json
import logging
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

MEMORY_TYPES = {"context", "pattern", "preference", "decision"}
DEFAULT_LIMIT = 10
CONTEXT_TTL_DAYS = 90  # context memories expire after 90 days
PATTERN_TTL_DAYS = None  # patterns are permanent


async def remember(
    user_id: str,
    memory_type: str,
    content: str,
    entities: list[str] | None = None,
    source: str = "chat",
    source_id: str = "",
    ttl_days: int | None = None,
) -> int | None:
    """Save a memory. Returns memory ID or None if DB unavailable."""
    if memory_type not in MEMORY_TYPES:
        log.warning("Invalid memory_type: %s", memory_type)
        return None

    if ttl_days is None:
        ttl_days = CONTEXT_TTL_DAYS if memory_type == "context" else PATTERN_TTL_DAYS

    expires_at = (
        datetime.utcnow() + timedelta(days=ttl_days) if ttl_days else None
    )

    try:
        from src.storage import get_pool

        pool = await get_pool()
        row = await pool.fetchrow(
            """
            INSERT INTO mi_user_memory
                (user_id, memory_type, content, entities, source, source_id, expires_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id
            """,
            user_id,
            memory_type,
            content,
            json.dumps(entities or [], ensure_ascii=False),
            source,
            source_id,
        )
        return row["id"]
    except Exception as e:
        log.warning("Memory save failed (degraded mode): %s", e)
        return None


async def recall(
    user_id: str,
    memory_type: str | None = None,
    query: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[dict]:
    """Retrieve relevant memories for a user.

    - memory_type: filter by type (None = all)
    - query: text search in content + entities
    - limit: max results
    Returns [] if DB unavailable (degraded mode).
    """
    try:
        from src.storage import get_pool

        pool = await get_pool()

        conditions = ["user_id = $1", "(expires_at IS NULL OR expires_at > NOW())"]
        params: list = [user_id]
        idx = 2

        if memory_type:
            conditions.append(f"memory_type = ${idx}")
            params.append(memory_type)
            idx += 1

        if query:
            conditions.append(
                f"(content ILIKE ${idx} OR entities::text ILIKE ${idx})"
            )
            params.append(f"%{query}%")
            idx += 1

        where = " AND ".join(conditions)
        rows = await pool.fetch(
            f"""
            SELECT id, memory_type, content, entities, source, source_id,
                   relevance, created_at
            FROM mi_user_memory
            WHERE {where}
            ORDER BY relevance DESC, created_at DESC
            LIMIT {limit}
            """,
            *params,
        )
        return [
            {
                "id": r["id"],
                "type": r["memory_type"],
                "content": r["content"],
                "entities": json.loads(r["entities"]) if r["entities"] else [],
                "source": r["source"],
                "relevance": r["relevance"],
                "created_at": r["created_at"].isoformat(),
            }
            for r in rows
        ]
    except Exception as e:
        log.warning("Memory recall failed (degraded mode): %s", e)
        return []


async def build_context_prompt(user_id: str, current_query: str) -> str:
    """Build a context block for system prompt from user memories.

    Returns empty string if no relevant memories (or DB unavailable).
    """
    memories = await recall(user_id, query=current_query, limit=5)
    if not memories:
        return ""

    lines = ["[User context from previous sessions:]"]
    for m in memories:
        date = m["created_at"][:10]
        lines.append(f"- [{date}] ({m['type']}) {m['content']}")

    return "\n".join(lines)


async def extract_and_save(
    user_id: str,
    chat_message: str,
    ai_response: str,
    source_id: str = "",
) -> int:
    """Auto-extract entities from a chat exchange and save as memory.

    Extracts: people names, decisions, action items.
    Returns count of memories saved.
    """
    saved = 0

    # Simple entity extraction (names in Cyrillic with capital letter)
    import re

    people = re.findall(r"[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+){1,2}", chat_message)
    people = list(set(people))

    if people:
        content = f"Обсуждались: {', '.join(people)}"
        mid = await remember(
            user_id, "context", content,
            entities=[p.lower() for p in people],
            source="chat", source_id=source_id,
        )
        if mid:
            saved += 1

    # Detect decisions (heuristic: "решили", "договорились", "план:")
    decision_markers = ["решили", "договорились", "план:", "итог:", "вывод:"]
    for marker in decision_markers:
        if marker in ai_response.lower():
            # Extract the sentence containing the marker
            for sentence in ai_response.split("."):
                if marker in sentence.lower():
                    mid = await remember(
                        user_id, "decision", sentence.strip(),
                        entities=[p.lower() for p in people],
                        source="chat", source_id=source_id,
                    )
                    if mid:
                        saved += 1
                    break
            break

    return saved


async def save_session_backup(user_id: str, session_data: dict) -> bool:
    """Save full session snapshot for disaster recovery."""
    try:
        from src.storage import get_pool

        pool = await get_pool()
        await pool.execute(
            """
            INSERT INTO mi_user_sessions (user_id, session_data)
            VALUES ($1, $2)
            """,
            user_id,
            json.dumps(session_data, ensure_ascii=False, default=str),
        )
        return True
    except Exception as e:
        log.warning("Session backup failed: %s", e)
        return False


async def get_session_backup(user_id: str) -> dict | None:
    """Restore latest session backup."""
    try:
        from src.storage import get_pool

        pool = await get_pool()
        row = await pool.fetchrow(
            """
            SELECT session_data FROM mi_user_sessions
            WHERE user_id = $1
            ORDER BY created_at DESC LIMIT 1
            """,
            user_id,
        )
        return json.loads(row["session_data"]) if row else None
    except Exception as e:
        log.warning("Session restore failed: %s", e)
        return None


async def decay_relevance(older_than_days: int = 30, factor: float = 0.9):
    """Reduce relevance of old memories (temporal decay). Run periodically."""
    try:
        from src.storage import get_pool

        pool = await get_pool()
        result = await pool.execute(
            """
            UPDATE mi_user_memory
            SET relevance = relevance * $1
            WHERE created_at < NOW() - INTERVAL '1 day' * $2
              AND relevance > 0.1
              AND expires_at IS NULL
            """,
            factor,
            older_than_days,
        )
        log.info("Memory decay: %s", result)
    except Exception as e:
        log.warning("Memory decay failed: %s", e)


async def cleanup_expired():
    """Delete expired memories. Run periodically."""
    try:
        from src.storage import get_pool

        pool = await get_pool()
        result = await pool.execute(
            "DELETE FROM mi_user_memory WHERE expires_at IS NOT NULL AND expires_at < NOW()"
        )
        log.info("Memory cleanup: %s", result)
    except Exception as e:
        log.warning("Memory cleanup failed: %s", e)
