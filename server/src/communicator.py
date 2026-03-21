"""Communication Assistant — adapt messages based on recipient psychotype.

Core VisionFlow feature: user pastes context (email thread or transcribed call),
selects recipient, gets an adapted response suggestion.
"""

import logging
import os

import anthropic

from src.config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL

log = logging.getLogger(__name__)


ADAPTATION_PROMPT = """Ты — эксперт по деловым коммуникациям. Твоя задача — помочь написать сообщение конкретному человеку, адаптированное под его стиль восприятия.

ПРОФИЛЬ ПОЛУЧАТЕЛЯ (как с ним общаться):
{guide}

КОНТЕКСТ (предыдущая переписка или расшифровка разговора):
{context}

ЗАДАЧА ОТПРАВИТЕЛЯ:
{task}

ЦЕЛЬ СООБЩЕНИЯ: {goal}
ТИП: {msg_type}

Напиши адаптированное сообщение от лица отправителя. Учти:
1. Стиль подачи, тон и объём по профилю получателя
2. Контекст предыдущего общения
3. Цель отправителя

Правила:
- Пиши как живой человек, НЕ как ИИ
- Никакого markdown — чистый текст для копирования в почту/чат
- Без шаблонных фраз ("в рамках", "хотелось бы отметить")
- Длина по профилю: если получатель предпочитает коротко — пиши коротко

Верни ТОЛЬКО текст сообщения, без пояснений."""


ADAPTATION_WITH_NOTES_PROMPT = """Ты — эксперт по деловым коммуникациям. Твоя задача — помочь написать сообщение конкретному человеку, адаптированное под его стиль восприятия.

ПРОФИЛЬ ПОЛУЧАТЕЛЯ (как с ним общаться):
{guide}

КОНТЕКСТ (предыдущая переписка или расшифровка разговора):
{context}

ЗАДАЧА ОТПРАВИТЕЛЯ:
{task}

ЦЕЛЬ СООБЩЕНИЯ: {goal}
ТИП: {msg_type}

Напиши адаптированное сообщение от лица отправителя. Учти:
1. Стиль подачи, тон и объём по профилю получателя
2. Контекст предыдущего общения
3. Цель отправителя

Правила:
- Пиши как живой человек, НЕ как ИИ
- Никакого markdown — чистый текст для копирования в почту/чат
- Без шаблонных фраз ("в рамках", "хотелось бы отметить")
- Длина по профилю: если получатель предпочитает коротко — пиши коротко

Верни:

СООБЩЕНИЕ:
(полный текст адаптированного сообщения)

ЗАМЕТКИ:
- (2-3 пункта: что адаптировано и почему — для понимания отправителя)"""


async def adapt_message(
    guide: str,
    context: str,
    task: str,
    goal: str = "respond",
    msg_type: str = "email",
    include_notes: bool = False,
) -> dict:
    """Generate an adapted message for a specific recipient.

    Args:
        guide: communication guide text for the recipient
        context: previous correspondence or transcribed call
        task: what the sender wants to achieve
        goal: respond | request | persuade | inform | escalate
        msg_type: email | chat | letter
        include_notes: if True, also return adaptation notes

    Returns:
        {"message": "adapted text", "notes": "what was adapted" | None,
         "tokens": {"input": N, "output": N}}
    """
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    prompt_template = ADAPTATION_WITH_NOTES_PROMPT if include_notes else ADAPTATION_PROMPT
    prompt = prompt_template.format(
        guide=guide[:4000],
        context=context[:8000],
        task=task,
        goal=goal,
        msg_type=msg_type,
    )

    model = os.environ.get("ANTHROPIC_MODEL", ANTHROPIC_MODEL)
    log.info("Adapting message for %s (goal=%s, type=%s, model=%s)", goal, msg_type, model, model)

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=model,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )

    result_text = response.content[0].text
    tokens = {
        "input": response.usage.input_tokens,
        "output": response.usage.output_tokens,
    }

    log.info("Adaptation complete: %d chars, %d+%d tokens", len(result_text), tokens["input"], tokens["output"])

    # Parse message and notes if include_notes
    message = result_text
    notes = None

    if include_notes and "СООБЩЕНИЕ:" in result_text:
        parts = result_text.split("ЗАМЕТКИ:")
        message = parts[0].replace("СООБЩЕНИЕ:", "").strip()
        if len(parts) > 1:
            notes = parts[1].strip()

    return {"message": message, "notes": notes, "tokens": tokens}
