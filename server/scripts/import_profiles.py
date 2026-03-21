"""Import profiles from tss-ops knowledge base into PostgreSQL.

Usage:
    python scripts/import_profiles.py --knowledge-base /path/to/06-KNOWLEDGE-BASE/people/

    # Grant access to a user:
    python scripts/import_profiles.py --grant khrenova melnikov-anatolij-igorevich

    # Grant access to multiple profiles:
    python scripts/import_profiles.py --grant khrenova melnikov-anatolij-igorevich titchenko-aleksej
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")

import asyncpg
from src.config import POSTGRES_DSN


async def import_from_knowledge_base(kb_path: str):
    """Import all profiles from knowledge base directory."""
    people_dir = Path(kb_path)
    if not people_dir.exists():
        print(f"✗ Directory not found: {people_dir}")
        return

    conn = await asyncpg.connect(POSTGRES_DSN)

    # Ensure tables exist
    from src.profiles import PROFILES_SCHEMA
    await conn.execute(PROFILES_SCHEMA)

    imported = 0
    skipped = 0

    for person_dir in sorted(people_dir.iterdir()):
        if not person_dir.is_dir():
            continue
        if person_dir.name.startswith("_"):
            continue

        person_id = person_dir.name

        # Read guide
        guide_path = person_dir / "communication-guide.md"
        guide = ""
        if guide_path.exists():
            guide = guide_path.read_text(encoding="utf-8")

        # Read profile
        profile_path = person_dir / "psychotype-profile.md"
        profile_data = ""
        if profile_path.exists():
            profile_data = profile_path.read_text(encoding="utf-8")

        if not guide and not profile_data:
            skipped += 1
            continue

        # Extract display name from guide or profile
        display_name = _extract_name(guide or profile_data, person_id)

        # Extract role from profile
        role = _extract_role(profile_data)

        await conn.execute(
            """
            INSERT INTO mi_profiles (id, display_name, role, department, guide, profile_data, updated_at)
            VALUES ($1, $2, $3, '', $4, $5, NOW())
            ON CONFLICT (id) DO UPDATE SET
                display_name = $2, role = $3, guide = $4, profile_data = $5, updated_at = NOW()
            """,
            person_id, display_name, role, guide, profile_data,
        )
        imported += 1
        print(f"  ✓ {display_name} ({person_id})")

    await conn.close()
    print(f"\n✓ Imported: {imported}, Skipped (no data): {skipped}")


async def grant_access(user_id: str, profile_ids: list[str]):
    """Grant a user access to specific profiles."""
    conn = await asyncpg.connect(POSTGRES_DSN)

    from src.profiles import PROFILES_SCHEMA
    await conn.execute(PROFILES_SCHEMA)

    for pid in profile_ids:
        # Check profile exists
        exists = await conn.fetchval("SELECT EXISTS(SELECT 1 FROM mi_profiles WHERE id = $1)", pid)
        if not exists:
            print(f"  ✗ Profile not found: {pid}")
            continue

        await conn.execute(
            """
            INSERT INTO mi_profile_access (user_id, profile_id, access_level)
            VALUES ($1, $2, 'guide')
            ON CONFLICT (user_id, profile_id) DO UPDATE SET access_level = 'guide'
            """,
            user_id, pid,
        )
        name = await conn.fetchval("SELECT display_name FROM mi_profiles WHERE id = $1", pid)
        print(f"  ✓ {user_id} → {name} ({pid})")

    await conn.close()
    print(f"\n✓ Access granted")


def _extract_name(text: str, fallback: str) -> str:
    """Extract display name from profile header."""
    for line in text.split("\n")[:5]:
        line = line.strip()
        if line.startswith("# ") and ":" in line:
            # "# Руководство по коммуникации: Мельников Анатолий Игоревич"
            return line.split(":", 1)[1].strip()
        if line.startswith("# Психологический профиль"):
            return line.replace("# Психологический профиль:", "").replace("# Психологический профиль", "").strip()
    # Fallback: convert slug to name
    return fallback.replace("-", " ").title()


def _extract_role(text: str) -> str:
    """Extract role/position from profile text."""
    for line in text.split("\n")[:20]:
        if "Должность" in line and ":" in line:
            return line.split(":", 1)[1].strip().strip("*")
        if "Роль" in line and ":" in line and "PAEI" not in line:
            return line.split(":", 1)[1].strip().strip("*")
    return ""


def main():
    parser = argparse.ArgumentParser(description="Import profiles into VisionFlow")
    parser.add_argument("--knowledge-base", "-kb", help="Path to knowledge base people/ directory")
    parser.add_argument("--grant", nargs="+", help="Grant access: USER_ID PROFILE_ID [PROFILE_ID ...]")
    args = parser.parse_args()

    if args.knowledge_base:
        asyncio.run(import_from_knowledge_base(args.knowledge_base))
    elif args.grant and len(args.grant) >= 2:
        user_id = args.grant[0]
        profile_ids = args.grant[1:]
        asyncio.run(grant_access(user_id, profile_ids))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
