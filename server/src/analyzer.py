"""Claude API meeting analysis.

Sends transcript to Claude, returns structured analysis (summary, decisions, tasks, risks).
"""

import logging
from pathlib import Path

import anthropic

from src.config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL, ANALYSIS_MAX_TOKENS, PROMPTS_DIR

log = logging.getLogger(__name__)

_PROMPT_CACHE: str | None = None


def _load_prompt() -> str:
    global _PROMPT_CACHE
    if _PROMPT_CACHE is None:
        prompt_path = Path(PROMPTS_DIR) / "meeting_analysis.txt"
        if prompt_path.exists():
            _PROMPT_CACHE = prompt_path.read_text(encoding="utf-8")
            log.info("Loaded analysis prompt from %s", prompt_path)
        else:
            _PROMPT_CACHE = _DEFAULT_PROMPT
            log.warning("Prompt file not found, using default")
    return _PROMPT_CACHE


_DEFAULT_PROMPT = """Ты — опытный ассистент руководителя.
Проанализируй транскрипцию совещания и извлеки структурированную информацию.

ТРАНСКРИПЦИЯ:
{transcript}

Верни анализ в следующем формате:

## Краткое содержание
(2-3 предложения: о чём было совещание)

## Ключевые решения
- (каждое решение отдельным пунктом)

## Задачи
| # | Задача | Ответственный | Срок | Приоритет |
|---|--------|--------------|------|-----------|
| 1 | ... | ... | ... | Высокий/Средний/Низкий |

## Риски
- (каждый риск отдельным пунктом, если есть)

## Проблемы
- (каждая проблема отдельным пунктом, если есть)

## Требуют решения руководства
- (вопросы для эскалации, если есть)

## Следующие шаги
- (ближайшие действия по итогам совещания)

Если информация не упоминалась — не придумывай, напиши "Не обсуждалось".
Пиши на русском языке. Будь конкретен и лаконичен."""


async def analyze(transcript_text: str) -> str:
    """Analyze meeting transcript via Claude API.

    Returns: markdown-formatted analysis text.
    """
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    prompt = _load_prompt().replace("{transcript}", transcript_text)

    log.info("Analyzing transcript (%d chars) with %s...", len(transcript_text), ANTHROPIC_MODEL)

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=ANALYSIS_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )

    result = message.content[0].text
    log.info(
        "Analysis complete: %d chars, %d input + %d output tokens",
        len(result),
        message.usage.input_tokens,
        message.usage.output_tokens,
    )
    return result
