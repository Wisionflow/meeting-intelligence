"""Audio transcription using faster-whisper (local, free, GPU-accelerated)."""

import time
from pathlib import Path
from faster_whisper import WhisperModel
from src.config import WHISPER_MODEL, WHISPER_DEVICE, WHISPER_LANGUAGE


_model = None


def get_model() -> WhisperModel:
    """Load whisper model (lazy, loads once on first call)."""
    global _model
    if _model is None:
        print(f"Загрузка модели {WHISPER_MODEL} на {WHISPER_DEVICE}...")
        compute_type = "float16" if WHISPER_DEVICE == "cuda" else "int8"
        _model = WhisperModel(
            WHISPER_MODEL,
            device=WHISPER_DEVICE,
            compute_type=compute_type,
        )
        print("Модель загружена.")
    return _model


def transcribe_audio(audio_path: str | Path) -> dict:
    """Transcribe audio file to text.

    Returns dict with keys:
        - text: full transcription text
        - segments: list of {start, end, text} dicts
        - duration: audio duration in seconds
        - language: detected language
        - processing_time: how long transcription took
    """
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"Аудиофайл не найден: {audio_path}")

    model = get_model()
    print(f"Транскрибирую: {audio_path.name}")

    start_time = time.time()
    segments_gen, info = model.transcribe(
        str(audio_path),
        language=WHISPER_LANGUAGE,
        beam_size=3,
        vad_filter=True,           # Filter out silence
        vad_parameters=dict(
            min_silence_duration_ms=500,
        ),
    )

    segments = []
    full_text_parts = []
    for seg in segments_gen:
        segments.append({
            "start": seg.start,
            "end": seg.end,
            "text": seg.text.strip(),
        })
        full_text_parts.append(seg.text.strip())

    processing_time = time.time() - start_time
    full_text = " ".join(full_text_parts)

    print(f"Готово за {processing_time:.1f}с | "
          f"Длительность аудио: {info.duration:.0f}с | "
          f"Язык: {info.language} ({info.language_probability:.0%})")

    return {
        "text": full_text,
        "segments": segments,
        "duration": info.duration,
        "language": info.language,
        "processing_time": processing_time,
    }


def format_transcript_with_timestamps(segments: list[dict]) -> str:
    """Format segments as readable transcript with timestamps."""
    lines = []
    for seg in segments:
        start_min = int(seg["start"] // 60)
        start_sec = int(seg["start"] % 60)
        lines.append(f"[{start_min:02d}:{start_sec:02d}] {seg['text']}")
    return "\n".join(lines)
