"""First-run check — verify all dependencies are configured.

Run after setup:
    python scripts/check.py
"""

import asyncio
import sys

sys.path.insert(0, ".")


async def main():
    print("Meeting Intelligence — System Check\n")
    ok = True

    # 1. Python version
    py = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info >= (3, 10):
        print(f"  ✓ Python {py}")
    else:
        print(f"  ✗ Python {py} — need 3.10+")
        ok = False

    # 2. Groq API key
    from src.config import GROQ_API_KEY
    if GROQ_API_KEY:
        print("  ✓ GROQ_API_KEY set")
        # Test API
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://api.groq.com/openai/v1/models",
                    headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                )
                if resp.status_code == 200:
                    print("  ✓ Groq API responds")
                else:
                    print(f"  ✗ Groq API error: {resp.status_code}")
                    ok = False
        except Exception as e:
            print(f"  ✗ Groq API unreachable: {e}")
            ok = False
    else:
        print("  ✗ GROQ_API_KEY not set")
        ok = False

    # 3. Claude API key
    from src.config import ANTHROPIC_API_KEY
    if ANTHROPIC_API_KEY:
        print("  ✓ ANTHROPIC_API_KEY set")
    else:
        print("  ✗ ANTHROPIC_API_KEY not set")
        ok = False

    # 4. PostgreSQL
    from src.config import POSTGRES_DSN
    try:
        import asyncpg
        conn = await asyncpg.connect(POSTGRES_DSN)
        ver = await conn.fetchval("SELECT version()")
        await conn.close()
        print(f"  ✓ PostgreSQL connected ({ver.split(',')[0]})")

        # Check tables
        conn = await asyncpg.connect(POSTGRES_DSN)
        exists = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name = 'mi_meetings')"
        )
        await conn.close()
        if exists:
            print("  ✓ mi_meetings table exists")
        else:
            print("  ⚠ mi_meetings table not found — run: python scripts/setup_db.py")
            ok = False
    except Exception as e:
        print(f"  ✗ PostgreSQL: {e}")
        ok = False

    # 5. Prompts
    from pathlib import Path
    prompt = Path("prompts/meeting_analysis.txt")
    if prompt.exists():
        print(f"  ✓ Analysis prompt: {prompt}")
    else:
        print(f"  ⚠ Analysis prompt not found at {prompt} — will use default")

    # Summary
    print()
    if ok:
        print("✓ All checks passed. Ready to start.")
        print("  Run: python -m src.server")
    else:
        print("✗ Some checks failed. Fix issues above before starting.")

    return 0 if ok else 1


if __name__ == "__main__":
    code = asyncio.run(main())
    sys.exit(code)
