"""Meeting Intelligence Server — configuration.

All settings via environment variables (.env file).
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ─── Database ────────────────────────────────────────────────────────────────

POSTGRES_DSN = os.getenv("POSTGRES_DSN", "")

# ─── Groq (Whisper transcription) ────────────────────────────────────────────

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "whisper-large-v3")
GROQ_API_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

# ─── OpenAI (Whisper API fallback) ──────────────────────────────────────────

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# ─── Local Whisper (always available) ───────────────────────────────────────

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "large-v3")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")  # int8 for CPU, float16 for GPU

# ─── Claude (analysis) ──────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
ANALYSIS_MAX_TOKENS = int(os.getenv("ANALYSIS_MAX_TOKENS", "4096"))

# ─── Server ──────────────────────────────────────────────────────────────────

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8080"))
UPLOAD_MAX_SIZE_MB = int(os.getenv("UPLOAD_MAX_SIZE_MB", "100"))

# ─── Paths ───────────────────────────────────────────────────────────────────

PROMPTS_DIR = os.getenv("PROMPTS_DIR", "prompts")
TEMPLATES_DIR = os.getenv("TEMPLATES_DIR", "templates")
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/tmp/mi_uploads")

# ─── Email (optional) ───────────────────────────────────────────────────────

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "")
NOTIFY_EMAIL = os.getenv("NOTIFY_EMAIL", "")  # recipient for notifications

# ─── Whisper language ────────────────────────────────────────────────────────

WHISPER_LANGUAGE = os.getenv("WHISPER_LANGUAGE", "ru")

# ─── Audio ───────────────────────────────────────────────────────────────────

AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".ogg", ".flac", ".mp4", ".aac", ".wma", ".webm"}

# ─── Auth ──────────────────────────────────────────────────────────────────

# Format: "username:password,username2:password2"
_auth_raw = os.getenv("AUTH_USERS", "")
AUTH_USERS: dict[str, str] = {}
for pair in _auth_raw.split(","):
    pair = pair.strip()
    if ":" in pair:
        u, p = pair.split(":", 1)
        AUTH_USERS[u.strip()] = p.strip()

SESSION_SECRET = os.getenv("SESSION_SECRET", "visionflow-default-secret-change-me")
