"""VisionFlow — System Check.

Verifies all dependencies are configured.
Run after setup:
    python scripts/check.py
"""

import asyncio
import sys

sys.path.insert(0, ".")


async def main():
    print("VisionFlow — System Check\n")
    ok = True
    backends = 0

    # 1. Python version
    py = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info >= (3, 10):
        print(f"  ✓ Python {py}")
    else:
        print(f"  ✗ Python {py} — need 3.10+")
        ok = False

    # 2. Transcription backends
    print("\n  Transcription backends:")

    from src.config import GROQ_API_KEY, OPENAI_API_KEY

    if GROQ_API_KEY:
        print("  ✓ [1] Groq API key set (cloud, fast)")
        backends += 1
    else:
        print("  · [1] Groq — not configured (optional)")

    if OPENAI_API_KEY:
        print("  ✓ [2] OpenAI API key set (cloud, paid)")
        backends += 1
    else:
        print("  · [2] OpenAI — not configured (optional)")

    # Local whisper — always available
    try:
        from faster_whisper import WhisperModel
        print("  ✓ [3] Local faster-whisper installed (always available)")
        backends += 1
    except ImportError:
        print("  ✗ [3] faster-whisper not installed")
        ok = False

    print(f"  → {backends} backend(s) available")
    if backends == 0:
        print("  ✗ No transcription backends! At least faster-whisper required.")
        ok = False

    # 3. Claude API key
    print()
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

        conn = await asyncpg.connect(POSTGRES_DSN)
        tables = ["mi_meetings", "mi_profiles", "mi_communications"]
        for t in tables:
            exists = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name = $1)", t
            )
            if exists:
                count = await conn.fetchval(f"SELECT COUNT(*) FROM {t}")
                print(f"  ✓ {t}: {count} records")
            else:
                print(f"  ⚠ {t} not found — run: python scripts/setup_db.py")
        await conn.close()
    except Exception as e:
        print(f"  ✗ PostgreSQL: {e}")
        ok = False

    # 5. ffmpeg
    import shutil
    if shutil.which("ffmpeg"):
        print("  ✓ ffmpeg installed")
    else:
        print("  ✗ ffmpeg not found (required for audio processing)")
        ok = False

    # 6. Prompts
    from pathlib import Path
    prompt = Path("prompts/meeting_analysis.txt")
    if prompt.exists():
        print(f"  ✓ Analysis prompt loaded")
    else:
        print(f"  · Using default analysis prompt")

    # Summary
    print()
    if ok:
        print("✓ All checks passed. Ready to start.")
        print("  Run: python -m src.server")
    else:
        print("✗ Some checks failed. Fix issues above.")

    return 0 if ok else 1


if __name__ == "__main__":
    code = asyncio.run(main())
    sys.exit(code)
