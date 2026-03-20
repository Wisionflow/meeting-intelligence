"""Meeting Intelligence — CLI entry point.

Usage:
  python -X utf8 run.py                    # Process all new audio files
  python -X utf8 run.py --diarize          # + speaker diarization
  python -X utf8 run.py --file audio.m4a   # Process specific file
  python -X utf8 run.py --no-analysis      # Transcription only (no Claude)
  python -X utf8 run.py --watch            # Watch AUDIO_INBOX continuously
  python -X utf8 run.py --status           # Show inbox/transcripts status
  python -X utf8 run.py --apply-names transcript.md  # Interactive speaker naming
"""

import sys
import argparse

sys.stdout.reconfigure(encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(
        description="Meeting Intelligence — audio → transcript → analysis"
    )

    # ── Processing ────────────────────────────────────────────────────────────
    parser.add_argument("--diarize", action="store_true",
                        help="Включить диаризацию спикеров (требует HF_TOKEN в .env)")
    parser.add_argument("--no-analysis", action="store_true",
                        help="Только транскрипция, без анализа Claude")
    parser.add_argument("--file", metavar="PATH",
                        help="Обработать конкретный аудиофайл")
    parser.add_argument("--force", action="store_true",
                        help="Переобработать даже если транскрипт уже существует")

    # ── Monitoring ────────────────────────────────────────────────────────────
    parser.add_argument("--watch", action="store_true",
                        help="Следить за AUDIO_INBOX и обрабатывать новые файлы")

    # ── Speaker identification ────────────────────────────────────────────────
    parser.add_argument("--apply-names", metavar="TRANSCRIPT",
                        help="Интерактивно назначить имена спикерам в транскрипте")
    parser.add_argument("--speaker-status", action="store_true",
                        help="Показать статус идентификации спикеров")

    # ── Status ────────────────────────────────────────────────────────────────
    parser.add_argument("--status", action="store_true",
                        help="Показать состояние папок и статистику")

    args = parser.parse_args()

    # ─── Status ───────────────────────────────────────────────────────────────
    if args.status:
        _print_status()
        return

    # ─── Speaker identification ───────────────────────────────────────────────
    if args.apply_names:
        from src.pipeline import process_audio_file
        from pathlib import Path
        _apply_names(Path(args.apply_names))
        return

    if args.speaker_status:
        _speaker_status()
        return

    # ─── Watch mode ───────────────────────────────────────────────────────────
    if args.watch:
        _watch(diarize=args.diarize, no_analysis=args.no_analysis)
        return

    # ─── Single run ───────────────────────────────────────────────────────────
    from src.pipeline import run_pipeline
    run_pipeline(
        diarize=args.diarize,
        no_analysis=args.no_analysis,
        force=args.force,
        file=args.file,
    )


# ─── STATUS ───────────────────────────────────────────────────────────────────

def _print_status():
    from src.config import AUDIO_INBOX, AUDIO_PROCESSED, TRANSCRIPTS_DIR, AUDIO_EXTENSIONS, NOTION_ENABLED
    from src.pipeline import find_new_audio_files

    print(f"\n{'═'*60}")
    print("  MEETING INTELLIGENCE — STATUS")
    print(f"{'═'*60}")
    print(f"  AUDIO_INBOX:    {AUDIO_INBOX}")
    print(f"  TRANSCRIPTS_DIR:{TRANSCRIPTS_DIR}")
    print(f"  Notion:         {'✓ настроен' if NOTION_ENABLED else '✗ не настроен'}")

    pending = find_new_audio_files()
    transcripts = list(TRANSCRIPTS_DIR.rglob("*.md")) if TRANSCRIPTS_DIR.exists() else []

    print(f"\n  Ожидают обработки: {len(pending)}")
    for f in pending[:10]:
        print(f"    {f.name}")
    if len(pending) > 10:
        print(f"    ... и ещё {len(pending)-10}")

    print(f"\n  Транскриптов всего: {len(transcripts)}")
    print(f"{'═'*60}\n")


# ─── WATCH ────────────────────────────────────────────────────────────────────

def _watch(diarize: bool, no_analysis: bool):
    """Watch AUDIO_INBOX for new files and process them."""
    from src.config import AUDIO_INBOX, AUDIO_EXTENSIONS
    import time

    print(f"\n  Слежу за: {AUDIO_INBOX}")
    print("  Ctrl+C для остановки\n")

    seen: set[str] = set()

    try:
        while True:
            if AUDIO_INBOX.exists():
                for ext in AUDIO_EXTENSIONS:
                    for f in AUDIO_INBOX.rglob(f"*{ext}"):
                        key = str(f)
                        if key not in seen:
                            seen.add(key)
                            from src.pipeline import process_audio_file
                            process_audio_file(f, diarize=diarize, no_analysis=no_analysis)
            time.sleep(30)
    except KeyboardInterrupt:
        print("\n  Остановлено.")


# ─── SPEAKER NAMES ────────────────────────────────────────────────────────────

def _apply_names(transcript_path):
    """Interactive speaker name assignment."""
    import json

    sidecar = transcript_path.with_suffix(".speakers.json")
    if not sidecar.exists():
        print(f"  Файл спикеров не найден: {sidecar}")
        return

    data = json.loads(sidecar.read_text(encoding="utf-8"))
    speakers = data.get("speakers", {})

    if not speakers:
        print("  В транскрипте нет спикеров для идентификации.")
        return

    print(f"\n  Транскрипт: {transcript_path.name}")
    print("  Назначьте имена спикерам (Enter = пропустить):\n")

    changed = False
    for sp_id in sorted(speakers):
        current = speakers[sp_id] or "(не определён)"
        name = input(f"  {sp_id} [{current}]: ").strip()
        if name:
            speakers[sp_id] = name
            changed = True

    if changed:
        data["speakers"] = speakers
        sidecar.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

        # Update transcript markdown with real names
        if transcript_path.exists():
            text = transcript_path.read_text(encoding="utf-8")
            for sp_id, name in speakers.items():
                if name:
                    text = text.replace(f"{sp_id}:", f"{name}:")
            transcript_path.write_text(text, encoding="utf-8")
            print(f"\n  ✓ Имена обновлены в {transcript_path.name}")
    else:
        print("\n  Без изменений.")


def _speaker_status():
    """Show speaker identification status across all transcripts."""
    from src.config import TRANSCRIPTS_DIR
    import json

    if not TRANSCRIPTS_DIR.exists():
        print("  TRANSCRIPTS_DIR не существует.")
        return

    total = identified = unidentified = 0
    for sidecar in TRANSCRIPTS_DIR.rglob("*.speakers.json"):
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        for sp_id, name in data.get("speakers", {}).items():
            total += 1
            if name:
                identified += 1
            else:
                unidentified += 1

    pct = (identified / total * 100) if total else 0
    print(f"\n  Спикеров всего: {total}")
    print(f"  Идентифицировано: {identified} ({pct:.0f}%)")
    print(f"  Не определено: {unidentified}\n")


if __name__ == "__main__":
    main()
