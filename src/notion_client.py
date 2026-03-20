"""Notion API integration — create/update meeting records with full data."""

import re
from datetime import datetime
from pathlib import Path

import httpx

from src.config import NOTION_TOKEN, NOTION_MEETINGS_DB


def _headers() -> dict:
    if not NOTION_TOKEN:
        raise ValueError("NOTION_TOKEN не задан в .env")
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }


def _split_into_blocks(text: str, block_limit: int = 1800) -> list[str]:
    """Split long text into chunks that fit Notion's block text limit."""
    if len(text) <= block_limit:
        return [text]
    chunks = []
    while text:
        chunk = text[:block_limit]
        if len(text) > block_limit:
            last_newline = chunk.rfind("\n")
            if last_newline > block_limit // 2:
                chunk = text[:last_newline]
                text = text[last_newline + 1 :]
            else:
                text = text[block_limit:]
        else:
            text = ""
        chunks.append(chunk)
    return chunks


def _md_blocks(markdown: str) -> list[dict]:
    """Convert markdown text to Notion paragraph blocks."""
    blocks = []
    for chunk in _split_into_blocks(markdown):
        blocks.append(
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": chunk}}]
                },
            }
        )
    return blocks


# ---------------------------------------------------------------------------
# Data extraction from transcript markdown
# ---------------------------------------------------------------------------


def extract_participants(content: str) -> int:
    """Extract participant count from ## Участники section."""
    match = re.search(r"## Участники\n(.*?)\n\n---", content, re.DOTALL)
    if match:
        return sum(
            1
            for line in match.group(1).strip().split("\n")
            if "=" in line and "**" in line
        )
    return 0


def extract_duration(content: str) -> float:
    """Extract duration in minutes from metadata."""
    match = re.search(r"Длительность:\*\*\s*(\d+)\s*сек", content)
    if match:
        return float(match.group(1)) / 60
    return 0


def extract_date(content: str, filename: str) -> str:
    """Extract meeting date from filename (YYYYMMDD pattern)."""
    match = re.search(r"(\d{4})(\d{2})(\d{2})", filename)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    match = re.search(r"Дата обработки:\*\*\s*(\d{4}-\d{2}-\d{2})", content)
    if match:
        return match.group(1)
    return datetime.now().strftime("%Y-%m-%d")


def extract_section(content: str, section_name: str) -> str:
    """Extract content of a markdown section."""
    pattern = rf"## {re.escape(section_name)}\n(.*?)\n\n##"
    match = re.search(pattern, content, re.DOTALL)
    if match:
        text = match.group(1).strip()
        if "Не обсуждалось" in text or "не обсужда" in text.lower():
            return ""
        return text
    return ""


def count_list_items(text: str) -> int:
    """Count list items (- or | rows, excluding table headers)."""
    if not text:
        return 0
    count = sum(
        1
        for line in text.split("\n")
        if line.strip().startswith("-")
        or line.strip().startswith("•")
        or line.strip().startswith("|")
    )
    if "|---|" in text:
        count = max(0, count - 2)
    return count


def extract_all_data(content: str, filename: str) -> dict:
    """Extract all structured data from a transcript markdown file."""
    participants_count = extract_participants(content)
    duration = extract_duration(content)
    date = extract_date(content, filename)

    risks_text = extract_section(content, "Риски")
    problems_text = extract_section(content, "Проблемы")
    decisions_text = extract_section(content, "Ключевые решения")
    tasks_text = extract_section(content, "Задачи")
    steps_text = extract_section(content, "Следующие шаги")

    # Full analysis section
    analysis_start = content.find("## Анализ совещания")
    transcript_start = content.find("## Транскрипция со спикерами")
    analysis = ""
    if analysis_start != -1 and transcript_start != -1:
        analysis = content[analysis_start:transcript_start].strip()

    # Diarized transcript section
    transcript = ""
    plain_start = content.find("## Полная транскрипция")
    if transcript_start != -1 and plain_start != -1:
        transcript = content[transcript_start:plain_start].strip()
    elif transcript_start != -1:
        transcript = content[transcript_start:].strip()

    return {
        "date": date,
        "duration": duration,
        "participants_count": participants_count,
        "risks_count": count_list_items(risks_text),
        "problems_count": count_list_items(problems_text),
        "decisions_count": count_list_items(decisions_text),
        "tasks_count": count_list_items(tasks_text),
        "steps_count": count_list_items(steps_text),
        "risks_text": risks_text,
        "problems_text": problems_text,
        "analysis": analysis,
        "transcript": transcript,
    }


# ---------------------------------------------------------------------------
# Notion API operations
# ---------------------------------------------------------------------------


def _replace_page_children(page_id: str, new_children: list[dict]) -> None:
    """Delete all existing blocks from a page and add new children."""
    # 1. Get existing blocks
    resp = httpx.get(
        f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100",
        headers=_headers(),
        timeout=30,
    )
    if resp.status_code == 200:
        for block in resp.json().get("results", []):
            httpx.delete(
                f"https://api.notion.com/v1/blocks/{block['id']}",
                headers=_headers(),
                timeout=15,
            )

    # 2. Add new children (Notion API limits to 100 blocks per request)
    if new_children:
        for i in range(0, len(new_children), 100):
            batch = new_children[i : i + 100]
            httpx.patch(
                f"https://api.notion.com/v1/blocks/{page_id}/children",
                headers=_headers(),
                json={"children": batch},
                timeout=60,
            )


def find_existing_page(title: str) -> str | None:
    """Find existing Notion page by title. Returns page_id or None."""
    if not NOTION_TOKEN or not NOTION_MEETINGS_DB:
        return None

    search_term = title.split("(")[1].split(")")[0] if "(" in title else title
    payload = {
        "filter": {
            "property": "Название",
            "title": {"contains": search_term},
        }
    }

    resp = httpx.post(
        f"https://api.notion.com/v1/databases/{NOTION_MEETINGS_DB}/query",
        headers=_headers(),
        json=payload,
        timeout=30,
    )

    if resp.status_code == 200:
        for result in resp.json().get("results", []):
            title_items = result["properties"]["Название"]["title"]
            if title_items and title_items[0]["text"]["content"] == title:
                return result["id"]
    return None


def _build_properties(
    title: str,
    date: str,
    category: str,
    audio_path: str,
    data: dict,
) -> dict:
    """Build Notion properties dict with all numeric and text fields."""
    properties: dict = {
        "Название": {"title": [{"text": {"content": title}}]},
        "Дата": {"date": {"start": date}},
        "Обработано": {"checkbox": True},
    }

    if data["duration"] > 0:
        properties["Длительность (мин)"] = {"number": round(data["duration"], 1)}
    if category:
        properties["Категория"] = {
            "rich_text": [{"text": {"content": category}}]
        }
    if audio_path:
        properties["Аудиофайл"] = {
            "rich_text": [{"text": {"content": audio_path}}]
        }

    # Numeric fields
    for field, key in [
        ("Участники (кол-во)", "participants_count"),
        ("Риски (кол-во)", "risks_count"),
        ("Проблемы (кол-во)", "problems_count"),
        ("Решения (кол-во)", "decisions_count"),
        ("Задачи (кол-во)", "tasks_count"),
        ("Шаги (кол-во)", "steps_count"),
    ]:
        if data.get(key, 0) > 0:
            properties[field] = {"number": data[key]}

    return properties


def _build_children(
    audio_path: str,
    transcript_path: str,
    data: dict,
) -> list[dict]:
    """Build Notion page content blocks with risks, problems, analysis, transcript."""
    children: list[dict] = []

    # File links
    links_parts = []
    if audio_path:
        links_parts.append(f"Аудио: {audio_path}")
    if transcript_path:
        links_parts.append(f"Транскрипция: {transcript_path}")
    if links_parts:
        children.append(
            {
                "object": "block",
                "type": "callout",
                "callout": {
                    "icon": {"type": "emoji", "emoji": "🔗"},
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {"content": "\n".join(links_parts)},
                        }
                    ],
                },
            }
        )

    # Analysis (already contains risks, problems, decisions, tasks, steps)
    if data.get("analysis"):
        children.append(
            {
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"text": {"content": "Анализ совещания"}}]
                },
            }
        )
        children.extend(_md_blocks(data["analysis"]))

    # Transcript (limited)
    if data.get("transcript"):
        children.append(
            {
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"text": {"content": "Транскрипция"}}]
                },
            }
        )
        children.extend(_md_blocks(data["transcript"][:10000]))

    return children[:100]


def sync_transcript_to_notion(
    transcript_path: Path,
    title: str,
    audio_path: str = "",
    category: str = "",
) -> str | None:
    """Full sync: read transcript, extract data, create/update Notion page.

    This is the single entry point for all Notion syncing.
    Returns page URL or None.
    """
    if not NOTION_TOKEN or not NOTION_MEETINGS_DB:
        print("Notion не настроен — пропускаю.")
        return None

    content = transcript_path.read_text(encoding="utf-8")
    data = extract_all_data(content, transcript_path.name)

    properties = _build_properties(title, data["date"], category, audio_path, data)
    children = _build_children(audio_path, str(transcript_path), data)

    print(
        f"Notion sync: {title} | "
        f"участники={data['participants_count']}, "
        f"риски={data['risks_count']}, "
        f"проблемы={data['problems_count']}, "
        f"решения={data['decisions_count']}, "
        f"задачи={data['tasks_count']}, "
        f"шаги={data['steps_count']}"
    )

    # Check for existing page
    existing_id = find_existing_page(title)

    if existing_id:
        print(f"  Обновляю существующую страницу...")
        # Update properties
        resp = httpx.patch(
            f"https://api.notion.com/v1/pages/{existing_id}",
            headers=_headers(),
            json={"properties": properties},
            timeout=30,
        )
        if resp.status_code != 200:
            print(f"  Ошибка обновления: {resp.status_code} — {resp.text[:300]}")
            return None

        # Replace children: delete old blocks, add new ones
        _replace_page_children(existing_id, children)

        url = resp.json().get("url", "")
        print(f"  Notion обновлено: {url}")
        return url

    # Create new page
    print(f"  Создаю новую страницу...")
    payload = {
        "parent": {"database_id": NOTION_MEETINGS_DB},
        "properties": properties,
        "children": children,
    }

    resp = httpx.post(
        "https://api.notion.com/v1/pages",
        headers=_headers(),
        json=payload,
        timeout=60,
    )

    if resp.status_code == 200:
        url = resp.json().get("url", "")
        print(f"  Notion создано: {url}")
        return url

    # Fallback with minimal properties
    if resp.status_code == 400:
        print(f"  Ошибка 400, пробую с минимальными полями...")
        payload["properties"] = {
            "Название": properties["Название"],
            "Дата": properties["Дата"],
            "Обработано": properties["Обработано"],
        }
        resp2 = httpx.post(
            "https://api.notion.com/v1/pages",
            headers=_headers(),
            json=payload,
            timeout=60,
        )
        if resp2.status_code == 200:
            url = resp2.json().get("url", "")
            print(f"  Notion создано (минимальные поля): {url}")
            return url
        print(f"  Ошибка: {resp2.status_code} — {resp2.text[:300]}")
        return None

    print(f"  Ошибка: {resp.status_code} — {resp.text[:300]}")
    return None


# Keep old function as alias for backward compatibility
def create_meeting_page(**kwargs) -> str | None:
    """Legacy wrapper — use sync_transcript_to_notion instead."""
    # This is only called from process_audio_file before the file is fully written,
    # so it still uses the old approach. Full sync happens after file is saved.
    return None
