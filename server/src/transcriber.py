"""VisionFlow Transcription Engine.

Multi-backend transcription with automatic fallback:
  1. Groq API (fast, cloud) — if GROQ_API_KEY is set
  2. OpenAI Whisper API — if OPENAI_API_KEY is set
  3. faster-whisper (local CPU) — ALWAYS available, no API keys needed

Usage:
    result = await transcribe("/path/to/audio.m4a")
    # result = {"text": "...", "segments": [...], "duration": 123.4, "backend": "local"}
"""

import asyncio
import logging
import time
from pathlib import Path

from src.config import (
    GROQ_API_KEY, GROQ_API_URL, GROQ_MODEL,
    OPENAI_API_KEY,
    WHISPER_LANGUAGE, WHISPER_MODEL, WHISPER_COMPUTE_TYPE, WHISPER_DEVICE,
)

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════

async def transcribe(audio_path: str | Path, language: str | None = None) -> dict:
    """Transcribe audio file using the best available backend.

    Returns:
        {
            "text": "full transcript",
            "segments": [{"start": 0.0, "end": 2.5, "text": "..."}, ...],
            "language": "ru",
            "duration": 1234.5,
            "backend": "groq" | "openai" | "local",
            "processing_time": 12.3
        }
    """
    audio_path = Path(audio_path)
    lang = language or WHISPER_LANGUAGE

    file_size_mb = audio_path.stat().st_size / (1024 * 1024)
    log.info("Transcribing %s (%.1f MB)...", audio_path.name, file_size_mb)

    # Build backend chain
    backends = []
    if GROQ_API_KEY:
        backends.append(("groq", _transcribe_groq))
    if OPENAI_API_KEY:
        backends.append(("openai", _transcribe_openai))
    backends.append(("local", _transcribe_local))

    log.info("Available backends: %s", [b[0] for b in backends])

    # Try each backend
    for name, fn in backends:
        try:
            t0 = time.time()
            result = await fn(audio_path, lang)
            elapsed = time.time() - t0

            result["backend"] = name
            result["processing_time"] = round(elapsed, 1)

            log.info(
                "✓ Transcribed via %s: %d segments, %.0f sec audio, %.1f sec processing",
                name, len(result["segments"]), result["duration"], elapsed,
            )
            return result

        except Exception as e:
            log.warning("✗ Backend '%s' failed: %s", name, e)
            if name == backends[-1][0]:
                raise  # last backend — re-raise
            log.info("  Falling back to next backend...")

    raise RuntimeError("All transcription backends failed")


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


# ═══════════════════════════════════════════════════════════════════════════
# BACKEND: Groq (cloud, fast)
# ═══════════════════════════════════════════════════════════════════════════

async def _transcribe_groq(audio_path: Path, language: str) -> dict:
    """Groq Whisper API — ~10 seconds for 30 min audio."""
    import httpx

    file_size_mb = audio_path.stat().st_size / (1024 * 1024)
    if file_size_mb > 25:
        raise ValueError(f"File too large for Groq: {file_size_mb:.1f} MB (max 25 MB)")

    log.info("  [groq] Sending to Groq Whisper API...")

    async with httpx.AsyncClient(timeout=600) as client:
        with open(audio_path, "rb") as f:
            resp = await client.post(
                GROQ_API_URL,
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                files={"file": (audio_path.name, f, "audio/mpeg")},
                data={
                    "model": GROQ_MODEL,
                    "language": language,
                    "response_format": "verbose_json",
                    "timestamp_granularities[]": "segment",
                },
            )

    if resp.status_code != 200:
        raise RuntimeError(f"Groq API {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    return _normalize_openai_format(data, language)


# ═══════════════════════════════════════════════════════════════════════════
# BACKEND: OpenAI Whisper API (cloud, paid)
# ═══════════════════════════════════════════════════════════════════════════

async def _transcribe_openai(audio_path: Path, language: str) -> dict:
    """OpenAI Whisper API — $0.006/min."""
    import httpx

    file_size_mb = audio_path.stat().st_size / (1024 * 1024)
    if file_size_mb > 25:
        raise ValueError(f"File too large for OpenAI: {file_size_mb:.1f} MB (max 25 MB)")

    log.info("  [openai] Sending to OpenAI Whisper API...")

    async with httpx.AsyncClient(timeout=600) as client:
        with open(audio_path, "rb") as f:
            resp = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                files={"file": (audio_path.name, f, "audio/mpeg")},
                data={
                    "model": "whisper-1",
                    "language": language,
                    "response_format": "verbose_json",
                    "timestamp_granularities[]": "segment",
                },
            )

    if resp.status_code != 200:
        raise RuntimeError(f"OpenAI API {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    return _normalize_openai_format(data, language)


def _normalize_openai_format(data: dict, language: str) -> dict:
    """Normalize OpenAI/Groq response to our format."""
    segments = data.get("segments", [])
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
        "language": data.get("language", language),
        "duration": data.get("duration", 0),
    }


# ═══════════════════════════════════════════════════════════════════════════
# BACKEND: Local faster-whisper (CPU, always available)
# ═══════════════════════════════════════════════════════════════════════════

_local_model = None


def _get_local_model():
    """Lazy-load faster-whisper model (loads once, reuses)."""
    global _local_model
    if _local_model is None:
        from faster_whisper import WhisperModel

        model_size = WHISPER_MODEL
        device = WHISPER_DEVICE
        compute_type = WHISPER_COMPUTE_TYPE

        log.info("  [local] Loading faster-whisper model '%s' (device=%s, compute=%s)...",
                 model_size, device, compute_type)

        _local_model = WhisperModel(
            model_size,
            device=device,
            compute_type=compute_type,
        )
        log.info("  [local] Model loaded")

    return _local_model


async def _transcribe_local(audio_path: Path, language: str) -> dict:
    """Local faster-whisper — no API, no cost, CPU-based."""
    log.info("  [local] Transcribing locally with faster-whisper...")

    # Run in thread pool to not block the event loop
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _transcribe_local_sync, audio_path, language)
    return result


def _transcribe_local_sync(audio_path: Path, language: str) -> dict:
    """Synchronous local transcription (runs in thread)."""
    model = _get_local_model()

    segments_iter, info = model.transcribe(
        str(audio_path),
        language=language if language != "auto" else None,
        beam_size=5,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
    )

    segments = []
    full_text_parts = []

    for seg in segments_iter:
        segments.append({
            "start": round(seg.start, 2),
            "end": round(seg.end, 2),
            "text": seg.text.strip(),
        })
        full_text_parts.append(seg.text.strip())

    return {
        "text": " ".join(full_text_parts),
        "segments": segments,
        "language": info.language or language,
        "duration": info.duration or 0,
    }
