"""Groq Whisper API transcription.

Sends audio to Groq cloud API, returns transcript with timestamps.
Max file size: 25 MB per Groq limit.
"""

import httpx
import logging
from pathlib import Path

from src.config import GROQ_API_KEY, GROQ_API_URL, GROQ_MODEL, WHISPER_LANGUAGE

log = logging.getLogger(__name__)


async def transcribe(audio_path: str | Path, language: str | None = None) -> dict:
    """Transcribe audio file via Groq Whisper API.

    Returns:
        {
            "text": "full transcript text",
            "segments": [{"start": 0.0, "end": 2.5, "text": "Hello"}, ...],
            "language": "ru",
            "duration": 1234.5
        }
    """
    audio_path = Path(audio_path)
    lang = language or WHISPER_LANGUAGE

    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY not set")

    file_size_mb = audio_path.stat().st_size / (1024 * 1024)
    log.info("Transcribing %s (%.1f MB) via Groq Whisper...", audio_path.name, file_size_mb)

    if file_size_mb > 25:
        raise ValueError(f"File too large for Groq API: {file_size_mb:.1f} MB (max 25 MB)")

    async with httpx.AsyncClient(timeout=600) as client:
        with open(audio_path, "rb") as f:
            resp = await client.post(
                GROQ_API_URL,
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                files={"file": (audio_path.name, f, "audio/mpeg")},
                data={
                    "model": GROQ_MODEL,
                    "language": lang,
                    "response_format": "verbose_json",
                    "timestamp_granularities[]": "segment",
                },
            )

    if resp.status_code != 200:
        log.error("Groq API error %d: %s", resp.status_code, resp.text[:500])
        raise RuntimeError(f"Groq API error {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    segments = data.get("segments", [])
    duration = data.get("duration", 0)

    log.info("Transcription complete: %d segments, %.0f sec", len(segments), duration)

    return {
        "text": data.get("text", ""),
        "segments": [
            {
                "start": s.get("start", 0),
                "end": s.get("end", 0),
                "text": s.get("text", "").strip(),
            }
            for s in segments
        ],
        "language": data.get("language", lang),
        "duration": duration,
    }


def format_transcript(segments: list[dict]) -> str:
    """Format segments into readable timestamped transcript."""
    lines = []
    for seg in segments:
        start = seg["start"]
        mm, ss = divmod(int(start), 60)
        hh, mm = divmod(mm, 60)
        if hh > 0:
            ts = f"[{hh}:{mm:02d}:{ss:02d}]"
        else:
            ts = f"[{mm:02d}:{ss:02d}]"
        lines.append(f"{ts} {seg['text']}")
    return "\n".join(lines)
