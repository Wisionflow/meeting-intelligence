"""Meeting Intelligence — configuration.

All settings via environment variables (.env file).
No hardcoded paths or keys.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ─── Paths ────────────────────────────────────────────────────────────────────

# Folder where new audio files appear (monitored)
AUDIO_INBOX = Path(os.getenv("AUDIO_INBOX", "inbox/audio"))

# Folder where processed audio files are moved after transcription
AUDIO_PROCESSED = Path(os.getenv("AUDIO_PROCESSED", "inbox/processed"))

# Folder where transcripts (.md) are saved
TRANSCRIPTS_DIR = Path(os.getenv("TRANSCRIPTS_DIR", "transcripts"))

# Folder for speaker voice embeddings store
EMBEDDINGS_DIR = Path(os.getenv("EMBEDDINGS_DIR", "data/embeddings"))

# Supported audio file extensions
AUDIO_EXTENSIONS = {
    ".m4a", ".mp3", ".wav", ".ogg", ".flac", ".mp4", ".aac", ".wma"
}

# ─── Whisper ──────────────────────────────────────────────────────────────────

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "large-v3")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cuda")   # cuda | cpu
WHISPER_LANGUAGE = os.getenv("WHISPER_LANGUAGE", "ru") # ru | en | auto
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "float16")  # float16 | int8

# ─── AI APIs ─────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")

# ─── Notion (optional) ────────────────────────────────────────────────────────

NOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
NOTION_MEETINGS_DB = os.getenv("NOTION_MEETINGS_DB", "")   # Database ID
NOTION_PARENT_PAGE = os.getenv("NOTION_PARENT_PAGE", "")   # Parent page ID (fallback)

NOTION_ENABLED = bool(NOTION_TOKEN and NOTION_MEETINGS_DB)

# ─── Meeting categories ───────────────────────────────────────────────────────
# Optional: map subfolder names to human-readable categories.
# Format: "subfolder=Label,subfolder2=Label2"
# Example: "sales=Sales,finance=Finance,general=General"
# Leave empty to use folder names as-is.
_categories_raw = os.getenv("MEETING_CATEGORIES", "")
MEETING_CATEGORIES: dict[str, str] = {}
if _categories_raw:
    for pair in _categories_raw.split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            MEETING_CATEGORIES[k.strip()] = v.strip()

# ─── Analysis ─────────────────────────────────────────────────────────────────

# Path to custom analysis prompt (optional, uses built-in default if not set)
ANALYSIS_PROMPT_FILE = os.getenv("ANALYSIS_PROMPT_FILE", "")

# Max tokens for meeting analysis
ANALYSIS_MAX_TOKENS = int(os.getenv("ANALYSIS_MAX_TOKENS", "4096"))

# ─── Pipeline behaviour ───────────────────────────────────────────────────────

# Move processed audio to AUDIO_PROCESSED after transcription
MOVE_AFTER_PROCESSING = os.getenv("MOVE_AFTER_PROCESSING", "true").lower() == "true"

# Auto-diarize (detect speakers) — requires pyannote token
DIARIZE_BY_DEFAULT = os.getenv("DIARIZE_BY_DEFAULT", "false").lower() == "true"

# pyannote.audio HuggingFace token (required for diarization)
HF_TOKEN = os.getenv("HF_TOKEN", "")
