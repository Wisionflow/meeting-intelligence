"""Meeting transcript analysis using Claude API."""

from pathlib import Path

import anthropic
from src.config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL, ANALYSIS_MAX_TOKENS, ANALYSIS_PROMPT_FILE


_DEFAULT_PROMPT = """You are an experienced operations assistant.
Analyze the meeting transcript and extract structured information.

TRANSCRIPT:
{transcript}

Return the analysis in the following format:

## Summary
(2-3 sentences: what the meeting was about)

## Key Decisions
- (each decision as a separate bullet)

## Action Items
| # | Task | Responsible | Due | Priority |
|---|------|-------------|-----|----------|
| 1 | ... | ... | ... | High/Medium/Low |

## Risks
- (each risk as a separate bullet, if any)

## Issues
- (each issue as a separate bullet, if any)

## Escalations Required
- (questions requiring management decision, if any)

## Next Steps
- (immediate actions following the meeting)

If information was not mentioned — do not invent it, write "Not discussed".
Be specific and concise.
"""


def _load_prompt() -> str:
    """Load analysis prompt from file or return default."""
    if ANALYSIS_PROMPT_FILE:
        p = Path(ANALYSIS_PROMPT_FILE)
        if p.exists():
            return p.read_text(encoding="utf-8")
        print(f"  [analyzer] Файл промпта не найден: {ANALYSIS_PROMPT_FILE}, использую дефолтный")
    # Also check prompts/ directory relative to project root
    local = Path("prompts/meeting_analysis.txt")
    if local.exists():
        return local.read_text(encoding="utf-8")
    return _DEFAULT_PROMPT


def analyze_transcript(transcript_text: str) -> str:
    """Analyze meeting transcript using Claude API.

    Returns structured markdown analysis.
    """
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY not set in .env")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = _load_prompt()

    print("  [analyzer] Анализирую транскрипцию...")
    message = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=ANALYSIS_MAX_TOKENS,
        messages=[{
            "role": "user",
            "content": prompt.format(transcript=transcript_text),
        }],
    )

    print(f"  [analyzer] Готово. Токены: вход={message.usage.input_tokens}, выход={message.usage.output_tokens}")
    return message.content[0].text
