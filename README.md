# Meeting Intelligence

**Audio → Transcript → Analysis → Action Items**

Local pipeline for automatic meeting transcription, speaker identification, and AI analysis. No cloud transcription — your audio stays on your machine.

---

## What it does

1. **Picks up** audio files from a folder you specify
2. **Transcribes** with Whisper (local GPU, no API cost)
3. **Identifies speakers** with pyannote diarization + voice fingerprinting
4. **Analyzes** with Claude → summary, decisions, action items, risks
5. **Saves** structured markdown transcript
6. **Optionally syncs** to Notion

---

## Quick start

```bash
# 1. Clone and install
git clone https://github.com/Wisionflow/meeting-intelligence.git
cd meeting-intelligence
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# Edit .env — set ANTHROPIC_API_KEY, AUDIO_INBOX, TRANSCRIPTS_DIR

# 3. Drop audio files into AUDIO_INBOX, then run
python -X utf8 run.py
```

---

## Requirements

- Python 3.10+
- NVIDIA GPU recommended (Whisper large-v3 runs on CPU but slowly)
- CUDA 11.8+ for GPU acceleration
- `ANTHROPIC_API_KEY` for meeting analysis

---

## CLI

```bash
# Process all new audio files
python -X utf8 run.py

# + speaker diarization (who said what)
python -X utf8 run.py --diarize

# Process a specific file
python -X utf8 run.py --file meeting.m4a

# Transcription only, skip Claude analysis
python -X utf8 run.py --no-analysis

# Watch folder continuously (auto-process new files)
python -X utf8 run.py --watch

# Show inbox status
python -X utf8 run.py --status

# Assign speaker names after diarization
python -X utf8 run.py --apply-names transcripts/2026-03-20_meeting.md
```

---

## Configuration

All settings in `.env`. See [.env.example](.env.example) for full reference.

**Minimum required:**
```env
ANTHROPIC_API_KEY=sk-ant-...
AUDIO_INBOX=/path/to/audio/inbox
TRANSCRIPTS_DIR=/path/to/transcripts
```

**For diarization** (speaker identification):
```env
HF_TOKEN=hf_...   # HuggingFace token
                   # Accept terms: huggingface.co/pyannote/speaker-diarization-3.1
```

**For Notion sync:**
```env
NOTION_TOKEN=secret_...
NOTION_MEETINGS_DB=<database-id>
```

---

## Output format

Each processed audio produces two files:

**`transcripts/2026-03-20_meeting.md`**
```markdown
# Sales Meeting — 20.03.2026 14:30

**File:** meeting.m4a
**Duration:** 47.3 min

## Speakers
- SPEAKER_00: (not identified)
- SPEAKER_01: (not identified)

## Transcript

[00:00] Hello everyone...
[00:15] SPEAKER_01: Today we need to discuss...

---

## Analysis

## Summary
...

## Action Items
| # | Task | Responsible | Due | Priority |
...
```

**`transcripts/2026-03-20_meeting.speakers.json`** — speaker metadata sidecar

---

## Customizing the analysis prompt

Edit `prompts/meeting_analysis.txt` to change what Claude extracts.
The `{transcript}` placeholder is replaced with the full transcript text.

Or point to a custom file:
```env
ANALYSIS_PROMPT_FILE=/path/to/my_prompt.txt
```

---

## Meeting categories

Map subfolder names to readable labels:
```env
MEETING_CATEGORIES=sales=Sales,finance=Finance,ops=Operations
```

Files in `inbox/sales/` → category "Sales" in transcript header and Notion.

---

## Architecture

```
run.py                    ← CLI entry point
src/
  config.py               ← all settings from .env
  pipeline.py             ← main orchestrator
  transcriber.py          ← Whisper transcription
  diarizer.py             ← pyannote speaker diarization
  speaker_embeddings.py   ← voice fingerprinting (auto speaker ID)
  analyzer.py             ← Claude analysis
  notion_client.py        ← Notion sync (optional)
prompts/
  meeting_analysis.txt    ← editable analysis prompt
```

---

## Models used

| Component | Model | Cost |
|-----------|-------|------|
| Transcription | Whisper large-v3 (local) | Free |
| Diarization | pyannote/speaker-diarization-3.1 (local) | Free |
| Analysis | Claude Haiku (default) | ~$0.01–0.05/meeting |
| Speaker ID | wespeaker embeddings (local) | Free |

Switch analysis model in `.env`:
```env
ANTHROPIC_MODEL=claude-sonnet-4-6   # Better quality, higher cost
```
