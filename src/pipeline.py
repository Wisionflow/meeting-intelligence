"""Meeting Intelligence — core pipeline.

Stages:
  1. Find new audio files in AUDIO_INBOX
  2. Transcribe with Whisper
  3. Diarize speakers (optional, --diarize)
  4. Analyze with Claude → summary, decisions, tasks, risks
  5. Save markdown transcript + JSON sidecar
  6. Sync to Notion (optional, if configured)
  7. Move audio to AUDIO_PROCESSED
"""

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Iterator

from src.config import (
    AUDIO_INBOX,
    AUDIO_PROCESSED,
    AUDIO_EXTENSIONS,
    TRANSCRIPTS_DIR,
    NOTION_ENABLED,
    MEETING_CATEGORIES,
    MOVE_AFTER_PROCESSING,
    HF_TOKEN,
)
from src.transcriber import transcribe_audio, format_transcript_with_timestamps
from src.analyzer import analyze_transcript


# ─── File Discovery ───────────────────────────────────────────────────────────

def find_new_audio_files() -> list[Path]:
    """Find all unprocessed audio files in AUDIO_INBOX."""
    if not AUDIO_INBOX.exists():
        print(f"  [pipeline] AUDIO_INBOX не существует: {AUDIO_INBOX}")
        return []

    files = []
    for ext in AUDIO_EXTENSIONS:
        files.extend(AUDIO_INBOX.rglob(f"*{ext}"))

    # Exclude already-processed files (in AUDIO_PROCESSED subtree)
    processed_str = str(AUDIO_PROCESSED.resolve())
    files = [f for f in files if processed_str not in str(f.resolve())]

    return sorted(files)


def get_category(audio_path: Path) -> str:
    """Extract meeting category from folder hierarchy.

    Uses MEETING_CATEGORIES mapping if configured, otherwise uses parent folder name.
    """
    parts = audio_path.parts
    # Walk up from file, skip the filename itself
    for part in reversed(parts[:-1]):
        if part in MEETING_CATEGORIES:
            return MEETING_CATEGORIES[part]
        # Skip root-level AUDIO_INBOX folder
        inbox_parts = AUDIO_INBOX.parts
        if part not in inbox_parts:
            return part.replace("_", " ").replace("-", " ").title()
    return "General"


def _find_existing_transcript(audio_path: Path) -> Path | None:
    """Check if transcript already exists for this audio file."""
    if not TRANSCRIPTS_DIR.exists():
        return None
    stem = re.sub(r"[\s_\-]+", "_", audio_path.stem.lower())
    for md in TRANSCRIPTS_DIR.rglob("*.md"):
        if re.sub(r"[\s_\-]+", "_", md.stem.lower()) == stem:
            return md
    return None


# ─── Main Processing ──────────────────────────────────────────────────────────

def process_audio_file(
    audio_path: Path,
    diarize: bool = False,
    no_analysis: bool = False,
    force: bool = False,
) -> Path | None:
    """Process a single audio file through the full pipeline.

    Returns path to the generated transcript, or None on failure.
    """
    print(f"\n{'─'*60}")
    print(f"  Обрабатываю: {audio_path.name}")

    # Duplicate check
    if not force:
        existing = _find_existing_transcript(audio_path)
        if existing:
            print(f"  [pipeline] Транскрипт уже существует: {existing.name} — пропускаю")
            return existing

    # ── Stage 1: Transcription ────────────────────────────────────────────────
    print("  [1/4] Транскрипция (Whisper)...")
    result = transcribe_audio(str(audio_path))
    if not result:
        print("  [pipeline] ОШИБКА: транскрипция не удалась")
        return None

    # ── Stage 2: Diarization (optional) ──────────────────────────────────────
    transcript_text = ""
    speakers_data = {}

    if diarize and HF_TOKEN:
        print("  [2/4] Диаризация (pyannote)...")
        try:
            from src.diarizer import (
                diarize_audio_with_waveform,
                merge_whisper_with_speakers,
                format_diarized_transcript,
                get_speakers_list,
            )
            waveform, sample_rate = _load_audio(audio_path)
            speaker_segments = diarize_audio_with_waveform(waveform, sample_rate, HF_TOKEN)
            merged = merge_whisper_with_speakers(result["segments"], speaker_segments)
            transcript_text = format_diarized_transcript(merged)
            speakers_data = {
                "speakers": get_speakers_list(merged),
                "diarized": True,
            }
        except Exception as e:
            print(f"  [pipeline] Диаризация не удалась: {e} — продолжаю без неё")
            transcript_text = format_transcript_with_timestamps(result["segments"])
    elif diarize and not HF_TOKEN:
        print("  [2/4] Пропускаю диаризацию (HF_TOKEN не задан)")
        transcript_text = format_transcript_with_timestamps(result["segments"])
    else:
        print("  [2/4] Пропускаю диаризацию")
        transcript_text = format_transcript_with_timestamps(result["segments"])

    # ── Stage 3: Analysis ─────────────────────────────────────────────────────
    analysis_text = ""
    if not no_analysis:
        print("  [3/4] Анализ (Claude)...")
        try:
            analysis_text = analyze_transcript(transcript_text)
        except Exception as e:
            print(f"  [pipeline] Анализ не удался: {e}")
    else:
        print("  [3/4] Пропускаю анализ (--no-analysis)")

    # ── Stage 4: Save ─────────────────────────────────────────────────────────
    print("  [4/4] Сохраняю...")
    transcript_path = _save_transcript(
        audio_path=audio_path,
        transcript_text=transcript_text,
        analysis_text=analysis_text,
        metadata=result,
        speakers_data=speakers_data,
    )

    # ── Notion sync (optional) ────────────────────────────────────────────────
    if NOTION_ENABLED and analysis_text:
        try:
            from src.notion_client import sync_transcript_to_notion
            print("  [notion] Синхронизирую...")
            title = f"{get_category(audio_path)} — {transcript_path.stem}"
            sync_transcript_to_notion(
                transcript_path=transcript_path,
                title=title,
                audio_path=str(audio_path),
                category=get_category(audio_path),
            )
        except Exception as e:
            print(f"  [notion] Не удалось синхронизировать: {e}")

    # ── Move audio ────────────────────────────────────────────────────────────
    if MOVE_AFTER_PROCESSING:
        _move_to_processed(audio_path)

    print(f"  ✓ Готово: {transcript_path.name}")
    return transcript_path


def _load_audio(audio_path: Path):
    """Load audio for diarization."""
    try:
        from src.diarizer import _load_audio_pyav
        return _load_audio_pyav(str(audio_path))
    except Exception:
        import torchaudio
        waveform, sample_rate = torchaudio.load(str(audio_path))
        return waveform, sample_rate


# ─── Save ─────────────────────────────────────────────────────────────────────

def _save_transcript(
    audio_path: Path,
    transcript_text: str,
    analysis_text: str,
    metadata: dict,
    speakers_data: dict,
) -> Path:
    """Save transcript markdown + JSON sidecar."""
    import json

    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

    # Generate output path mirroring AUDIO_INBOX subfolder structure
    try:
        rel = audio_path.relative_to(AUDIO_INBOX)
        out_dir = TRANSCRIPTS_DIR / rel.parent
    except ValueError:
        out_dir = TRANSCRIPTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    stem = re.sub(r"[^\w\-]", "_", audio_path.stem)
    md_path = out_dir / f"{date_str}_{stem}.md"
    json_path = md_path.with_suffix(".speakers.json")

    category = get_category(audio_path)
    duration_min = round(metadata.get("duration", 0) / 60, 1)

    # ── Markdown ──────────────────────────────────────────────────────────────
    header = f"""# {category} — {now.strftime('%d.%m.%Y %H:%M')}

**Файл:** {audio_path.name}
**Длительность:** {duration_min} мин
**Язык:** {metadata.get('language', '?')}
**Дата:** {date_str}

"""
    speakers_section = ""
    if speakers_data.get("speakers"):
        speakers_section = "\n## Спикеры\n"
        for sp in speakers_data["speakers"]:
            speakers_section += f"- {sp['id']}: (не определён)\n"
        speakers_section += "\n"

    content = header + speakers_section + "## Транскрипция\n\n" + transcript_text
    if analysis_text:
        content += "\n\n---\n\n## Анализ\n\n" + analysis_text

    md_path.write_text(content, encoding="utf-8")

    # ── JSON sidecar ──────────────────────────────────────────────────────────
    sidecar = {
        "audio_file": str(audio_path),
        "transcript_file": str(md_path),
        "date": date_str,
        "category": category,
        "duration_seconds": metadata.get("duration", 0),
        "language": metadata.get("language", ""),
        "diarized": speakers_data.get("diarized", False),
        "speakers": {sp["id"]: None for sp in speakers_data.get("speakers", [])},
        "created_at": now.isoformat(),
    }
    json_path.write_text(
        json.dumps(sidecar, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return md_path


# ─── Move audio ───────────────────────────────────────────────────────────────

def _move_to_processed(audio_path: Path) -> None:
    """Move audio file to AUDIO_PROCESSED preserving subfolder structure."""
    try:
        rel = audio_path.relative_to(AUDIO_INBOX)
    except ValueError:
        rel = Path(audio_path.name)

    dest = AUDIO_PROCESSED / rel
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = dest.parent / f"{dest.stem}_{ts}{dest.suffix}"

    shutil.move(str(audio_path), str(dest))
    print(f"  [pipeline] → перемещён в processed/")


# ─── Batch runner ─────────────────────────────────────────────────────────────

def run_pipeline(
    diarize: bool = False,
    no_analysis: bool = False,
    force: bool = False,
    file: str | None = None,
) -> int:
    """Run pipeline on all new audio files (or a specific file).

    Returns count of successfully processed files.
    """
    if file:
        files = [Path(file)]
    else:
        files = find_new_audio_files()

    if not files:
        print("  Нет новых аудиофайлов для обработки.")
        return 0

    print(f"\nНайдено файлов: {len(files)}")
    success = 0
    for audio_path in files:
        try:
            result = process_audio_file(
                audio_path, diarize=diarize, no_analysis=no_analysis, force=force
            )
            if result:
                success += 1
        except Exception as e:
            print(f"  ОШИБКА при обработке {audio_path.name}: {e}")

    print(f"\n{'─'*60}")
    print(f"Обработано: {success}/{len(files)}")
    return success
