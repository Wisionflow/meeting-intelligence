"""Communication Assistant — strategic communication advisor.

V2: Full 5-block strategic analysis based on psychoprofiles.
V1 (adapt_message): backward-compatible simple adaptation.
"""

import logging
import os
import re

import anthropic

from src.config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL

log = logging.getLogger(__name__)


# ─── V1 prompt (backward compat) ─────────────────────────────────────────────

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


# ─── V2 prompt: 5-block strategic analysis ────────────────────────────────────

STRATEGIC_PROMPT = """Ты — стратегический советник по коммуникации. Ты анализируешь ситуацию через психопрофили участников, строишь стратегию и объясняешь каждое решение.

ПРОФИЛЬ ПОЛУЧАТЕЛЯ:
{recipient_profile}

КАК ОБЩАТЬСЯ С ПОЛУЧАТЕЛЕМ:
{recipient_guide}

{sender_section}

{third_parties_section}

КОНТЕКСТ (переписка / расшифровка разговора / документы):
{context}

ЗАДАЧА ОТПРАВИТЕЛЯ: {task}
ЦЕЛЬ: {goal}
ТИП СООБЩЕНИЯ: {msg_type}

Дай стратегический анализ в 5 блоках. Формат — СТРОГО соблюдай XML-теги:

<block1>
Анализ ситуации и людей. 3-5 конкретных наблюдений.
Что важно для получателя (мотивация, страхи из DISC).
Где трение между профилями участников. Какую тактику использует получатель.
Что он реально защищает или добивается.
</block1>

<block2>
Стратегия коммуникации.
Что делать (конкретные приёмы). Что НЕ делать (ловушки).
Порядок аргументов. Тон и структура под DISC получателя.
{coalition_instruction}
</block2>

<block3>
Готовый текст сообщения. Полностью готов к отправке.
Правила:
- Пиши как живой человек, НЕ как ИИ
- Никакого markdown — чистый текст
- Без шаблонных фраз ("в рамках", "хотелось бы отметить", "важно подчеркнуть")
- Разная длина предложений, допустимы разговорные обороты
- Длина по профилю получателя
- Если email — включи тему письма в первой строке в формате "Тема: ..."
- Подпись — по контексту

После текста на отдельной строке дай короткий комментарий (1-2 предложения): что в тексте необычно или важно. Начни комментарий с "---" на отдельной строке.
</block3>

<block4>
Объяснение — почему именно так.
Для каждого ключевого решения в тексте — объяснение логики.
Формат: "[Элемент текста]" — потому что [логика из профиля получателя]
3-5 объяснений.
</block4>

<block5>
Что делать если не сработает.
Признаки что стратегия не работает (конкретные действия получателя).
Следующий шаг (эскалация / другой тон / другой канал).
Что точно НЕ делать на следующем шаге.
{escalation_instruction}
</block5>"""


# ─── Parse helpers ────────────────────────────────────────────────────────────

def _parse_blocks(text: str) -> dict:
    """Parse 5 blocks from Claude response using XML tags."""
    blocks = {}
    for i in range(1, 6):
        tag = f"block{i}"
        pattern = rf"<{tag}>(.*?)</{tag}>"
        match = re.search(pattern, text, re.DOTALL)
        blocks[tag] = match.group(1).strip() if match else ""

    # Extract clean message from block3 (before the --- comment)
    if blocks.get("block3"):
        parts = blocks["block3"].rsplit("\n---\n", 1)
        blocks["message"] = parts[0].strip()
        blocks["message_comment"] = parts[1].strip() if len(parts) > 1 else ""
    else:
        blocks["message"] = ""
        blocks["message_comment"] = ""

    return blocks


def _disc_distance(profile_a: str, profile_b: str) -> float | None:
    """Rough DISC distance between two profiles. Returns None if can't parse."""
    def extract_disc(text: str) -> dict | None:
        vals = {}
        for dim in ["D", "I", "S", "C"]:
            match = re.search(
                rf"\*\*{dim}\*\*.*?\|\s*(\d+)%",
                text,
            )
            if match:
                vals[dim] = int(match.group(1))
        return vals if len(vals) == 4 else None

    a = extract_disc(profile_a)
    b = extract_disc(profile_b)
    if not a or not b:
        return None

    # Euclidean distance normalized to 0-100
    dist = sum((a[d] - b[d]) ** 2 for d in "DISC") ** 0.5
    return round(dist, 1)


# ─── V1: Simple adaptation (backward compat) ─────────────────────────────────

async def adapt_message(
    guide: str,
    context: str,
    task: str,
    goal: str = "respond",
    msg_type: str = "email",
    include_notes: bool = False,
) -> dict:
    """Generate an adapted message for a specific recipient (V1)."""
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    prompt = ADAPTATION_PROMPT.format(
        guide=guide[:4000],
        context=context[:8000],
        task=task,
        goal=goal,
        msg_type=msg_type,
    )

    model = os.environ.get("ANTHROPIC_MODEL", ANTHROPIC_MODEL)
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

    return {"message": result_text, "notes": None, "tokens": tokens}


# ─── V2: Strategic 5-block analysis ──────────────────────────────────────────

async def strategic_analysis(
    recipient_guide: str,
    recipient_profile: str,
    context: str,
    task: str,
    goal: str = "respond",
    msg_type: str = "email",
    sender_profile: str | None = None,
    third_parties: list[dict] | None = None,
) -> dict:
    """Generate full 5-block strategic analysis.

    Args:
        recipient_guide: communication-guide.md content
        recipient_profile: psychotype-profile.md content (DISC, Big5)
        context: correspondence, transcription, or document text
        task: what sender wants to achieve
        goal: respond | request | persuade | inform | escalate | deescalate
        msg_type: email | chat | letter | conversation
        sender_profile: sender's psychotype-profile.md (optional)
        third_parties: list of {"name": ..., "profile": ..., "guide": ...}

    Returns:
        {"blocks": {block1..block5, message, message_comment},
         "message": str, "third_parties_used": [...],
         "tokens": {"input": N, "output": N}}
    """
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    # Build sender section
    sender_section = ""
    if sender_profile:
        sender_section = f"ПРОФИЛЬ ОТПРАВИТЕЛЯ:\n{sender_profile[:3000]}"

    # Build third-party section + coalition analysis
    third_parties_section = ""
    coalition_instruction = ""
    escalation_instruction = ""
    third_party_names = []

    if third_parties:
        parts = []
        for tp in third_parties:
            third_party_names.append(tp["name"])
            parts.append(
                f"ПРОФИЛЬ ТРЕТЬЕЙ СТОРОНЫ — {tp['name']}:\n"
                f"{tp.get('profile', '')[:2000]}\n"
                f"Как общаться: {tp.get('guide', '')[:1000]}"
            )
        third_parties_section = "\n\n".join(parts)

        # Check DISC proximity for coalition risk
        for tp in third_parties:
            dist = _disc_distance(recipient_profile, tp.get("profile", ""))
            if dist is not None and dist < 25:
                coalition_instruction += (
                    f"\nВАЖНО: Профили получателя и {tp['name']} БЛИЗКИ "
                    f"(DISC-расстояние {dist}). Есть риск коалиции. "
                    f"Явно укажи этот риск и как его учесть в стратегии."
                )
                escalation_instruction += (
                    f"\nЕсли в план Б входит эскалация к {tp['name']} — "
                    f"учти что их профили близки с получателем. "
                    f"Риск: они могут объединиться. "
                    f"Подготовь материал именно под профиль {tp['name']}."
                )

    if not coalition_instruction:
        coalition_instruction = ""
    if not escalation_instruction:
        escalation_instruction = ""

    prompt = STRATEGIC_PROMPT.format(
        recipient_profile=recipient_profile[:5000],
        recipient_guide=recipient_guide[:3000],
        sender_section=sender_section,
        third_parties_section=third_parties_section,
        context=context[:10000],
        task=task,
        goal=goal,
        msg_type=msg_type,
        coalition_instruction=coalition_instruction,
        escalation_instruction=escalation_instruction,
    )

    model = os.environ.get("ANTHROPIC_MODEL", ANTHROPIC_MODEL)
    log.info(
        "Strategic analysis: goal=%s, type=%s, model=%s, third_parties=%s",
        goal, msg_type, model, third_party_names or "none",
    )

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    result_text = response.content[0].text
    tokens = {
        "input": response.usage.input_tokens,
        "output": response.usage.output_tokens,
    }

    blocks = _parse_blocks(result_text)

    log.info(
        "Strategic analysis complete: %d chars, %d+%d tokens, blocks=%s",
        len(result_text),
        tokens["input"],
        tokens["output"],
        [k for k, v in blocks.items() if v and k.startswith("block")],
    )

    return {
        "blocks": blocks,
        "message": blocks.get("message", ""),
        "third_parties_used": third_party_names,
        "tokens": tokens,
    }


# ─── V3: Chat-style analysis (natural language input) ────────────────────────

CHAT_SYSTEM_PROMPT = """Ты — стратегический советник по коммуникации в организации.

Пользователь описывает ситуацию свободным текстом. Ты анализируешь через психопрофили участников и даёшь стратегический анализ.

ДОСТУПНЫЕ ПРОФИЛИ ЛЮДЕЙ В ОРГАНИЗАЦИИ:
{profiles_section}

ПРАВИЛА:
- Если пользователь описывает ситуацию впервые — дай полный анализ в 5 блоках
- Если добавляет контекст к кейсу — обнови только изменившиеся блоки
- Если задаёт вопрос — ответь на вопрос, при необходимости обнови блоки
- Если в тексте упомянут человек с профилем — используй его DISC/Big5 для анализа
- Если упомянут человек БЕЗ профиля — попроси описать его в 2-3 предложениях

ФОРМАТ ВЫВОДА — строго XML-теги:

<block1>
Анализ ситуации и людей. 3-5 конкретных наблюдений.
Что важно для получателя (мотивация, страхи из DISC).
Где трение между профилями. Какую тактику использует получатель.
</block1>

<block2>
Стратегия коммуникации.
Что делать (конкретные приёмы). Что НЕ делать (ловушки).
Порядок аргументов. Тон и структура.
Если обнаружен риск коалиции — указать явно.
</block2>

<block3>
Готовый текст сообщения. Полностью готов к отправке.
- Пиши как живой человек, НЕ как ИИ
- Никакого markdown — чистый текст
- Без шаблонных фраз ("в рамках", "хотелось бы отметить")
- Разная длина предложений, разговорные обороты допустимы
- Если email — включи "Тема: ..." в первой строке
- Подпись — по контексту

После текста на отдельной строке "---" и короткий комментарий (1-2 предложения).
</block3>

<block4>
Почему именно так. Для ключевых решений в тексте:
"[Элемент текста]" — потому что [логика из профиля]
3-5 объяснений.
</block4>

<block5>
Что делать если не сработает.
Признаки провала. Следующий шаг. Что НЕ делать.
Если эскалация — к кому, с чем, риски коалиции.
</block5>

ИСКЛЮЧЕНИЕ: если пользователь задаёт короткий вопрос или уточняет — можешь ответить без блоков, обычным текстом. Блоки выдавай только когда есть ситуация для анализа."""


async def chat_analysis(
    message: str,
    history: list[dict],
    profiles: list[dict],
) -> dict:
    """Chat-style strategic analysis. User writes naturally, AI understands.

    Args:
        message: user's latest message (free text)
        history: previous messages [{"role": "user"|"assistant", "content": ...}]
        profiles: detected profiles [{"name": ..., "profile": ..., "guide": ...}]

    Returns:
        {"blocks": {...} | None, "text": str, "third_parties_used": [...],
         "tokens": {"input": N, "output": N}}
    """
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    # Build profiles section
    profiles_section = ""
    profile_names = []
    if profiles:
        parts = []
        for p in profiles:
            profile_names.append(p["name"])
            section = f"--- {p['name']} ---\n"
            if p.get("profile"):
                section += p["profile"][:3000] + "\n"
            if p.get("guide"):
                section += "\nКак общаться:\n" + p["guide"][:1500]
            parts.append(section)
        profiles_section = "\n\n".join(parts)
    else:
        profiles_section = "(Профили не обнаружены в сообщении)"

    # Check coalition risks between detected profiles
    coalition_warnings = []
    for i, p1 in enumerate(profiles):
        for p2 in profiles[i + 1:]:
            dist = _disc_distance(p1.get("profile", ""), p2.get("profile", ""))
            if dist is not None and dist < 25:
                coalition_warnings.append(
                    f"ВНИМАНИЕ: Профили {p1['name']} и {p2['name']} близки "
                    f"(DISC-расстояние {dist}). Риск коалиции."
                )

    if coalition_warnings:
        profiles_section += "\n\n" + "\n".join(coalition_warnings)

    system = CHAT_SYSTEM_PROMPT.format(profiles_section=profiles_section)

    # Build messages array
    messages = []
    for h in history[-20:]:  # last 20 messages max
        messages.append({
            "role": h["role"],
            "content": h["content"][:5000],
        })
    messages.append({"role": "user", "content": message})

    model = os.environ.get("ANTHROPIC_MODEL", ANTHROPIC_MODEL)
    log.info(
        "Chat analysis: %d history msgs, %d profiles (%s), model=%s",
        len(history), len(profiles), profile_names, model,
    )

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system,
        messages=messages,
    )

    result_text = response.content[0].text
    tokens = {
        "input": response.usage.input_tokens,
        "output": response.usage.output_tokens,
    }

    # Try to parse blocks — may not have blocks if it's a simple Q&A
    blocks = _parse_blocks(result_text)
    has_blocks = any(blocks.get(f"block{i}") for i in range(1, 6))

    log.info(
        "Chat analysis complete: %d chars, %d+%d tokens, has_blocks=%s, profiles=%s",
        len(result_text), tokens["input"], tokens["output"],
        has_blocks, profile_names,
    )

    return {
        "blocks": blocks if has_blocks else None,
        "text": result_text if not has_blocks else blocks.get("message", ""),
        "raw": result_text,
        "third_parties_used": profile_names,
        "coalition_warnings": coalition_warnings,
        "tokens": tokens,
    }
