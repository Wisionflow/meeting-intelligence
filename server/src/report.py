"""HTML report generator for Meeting Intelligence.

Generates a clean, professional, self-contained HTML report.
No external dependencies — all CSS inline.
"""

import re
from datetime import datetime
from pathlib import Path

from src.config import TEMPLATES_DIR


def generate_html_report(
    filename: str,
    duration_sec: float,
    transcript_formatted: str,
    analysis_text: str,
    created_at: datetime | None = None,
) -> str:
    """Generate a self-contained HTML report."""
    template_path = Path(TEMPLATES_DIR) / "report.html"
    if template_path.exists():
        template = template_path.read_text(encoding="utf-8")
    else:
        template = _DEFAULT_TEMPLATE

    dt = created_at or datetime.now()
    date_str = dt.strftime("%d.%m.%Y %H:%M")

    duration_min = int(duration_sec // 60)
    duration_str = f"{duration_min} мин" if duration_min > 0 else f"{int(duration_sec)} сек"

    # Convert markdown analysis to HTML
    analysis_html = _md_to_html(analysis_text)

    # Convert transcript to HTML
    transcript_html = _transcript_to_html(transcript_formatted)

    html = template.replace("{{TITLE}}", _clean_filename(filename))
    html = html.replace("{{DATE}}", date_str)
    html = html.replace("{{DURATION}}", duration_str)
    html = html.replace("{{FILENAME}}", filename)
    html = html.replace("{{ANALYSIS}}", analysis_html)
    html = html.replace("{{TRANSCRIPT}}", transcript_html)

    return html


def _clean_filename(filename: str) -> str:
    name = Path(filename).stem
    name = re.sub(r"[-_]", " ", name)
    return name.strip()


def _md_to_html(md: str) -> str:
    """Minimal markdown to HTML for analysis sections."""
    lines = md.split("\n")
    html_parts = []
    in_table = False

    for line in lines:
        stripped = line.strip()

        if not stripped:
            if in_table:
                html_parts.append("</table>")
                in_table = False
            html_parts.append("")
            continue

        # Headers
        if stripped.startswith("## "):
            if in_table:
                html_parts.append("</table>")
                in_table = False
            html_parts.append(f'<h2>{stripped[3:]}</h2>')
            continue

        # Table separator row (|---|---|)
        if re.match(r"^\|[-\s|]+\|$", stripped):
            continue

        # Table header/body
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            if not in_table:
                html_parts.append('<table>')
                html_parts.append("<tr>" + "".join(f"<th>{c}</th>" for c in cells) + "</tr>")
                in_table = True
            else:
                html_parts.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
            continue

        # List items
        if stripped.startswith("- "):
            if in_table:
                html_parts.append("</table>")
                in_table = False
            html_parts.append(f"<li>{stripped[2:]}</li>")
            continue

        # Plain text
        if in_table:
            html_parts.append("</table>")
            in_table = False
        html_parts.append(f"<p>{stripped}</p>")

    if in_table:
        html_parts.append("</table>")

    return "\n".join(html_parts)


def _transcript_to_html(transcript: str) -> str:
    """Convert timestamped transcript to HTML."""
    lines = transcript.split("\n")
    html_parts = []
    for line in lines:
        if not line.strip():
            continue
        # Match [00:00] or [0:00:00] timestamp
        m = re.match(r"(\[[\d:]+\])\s*(.*)", line)
        if m:
            ts, text = m.group(1), m.group(2)
            html_parts.append(f'<div class="seg"><span class="ts">{ts}</span> {text}</div>')
        else:
            html_parts.append(f"<div>{line}</div>")
    return "\n".join(html_parts)


_DEFAULT_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{TITLE}} — Meeting Intelligence</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
    font-size: 14px; line-height: 1.6; color: #1a1a1a;
    max-width: 900px; margin: 0 auto; padding: 40px 24px;
    background: #fff;
  }
  .header {
    border-bottom: 2px solid #006699; padding-bottom: 16px; margin-bottom: 32px;
  }
  .header h1 { font-size: 24px; color: #006699; margin-bottom: 8px; }
  .meta { color: #666; font-size: 13px; }
  .meta span { margin-right: 24px; }

  h2 {
    font-size: 16px; color: #006699; margin: 28px 0 12px;
    padding-bottom: 4px; border-bottom: 1px solid #e0e0e0;
  }

  li { margin: 4px 0 4px 20px; }
  p { margin: 8px 0; }

  table {
    width: 100%; border-collapse: collapse; margin: 12px 0;
    font-size: 13px;
  }
  th {
    background: #f5f7fa; text-align: left; padding: 8px 12px;
    border: 1px solid #ddd; font-weight: 600;
  }
  td { padding: 8px 12px; border: 1px solid #ddd; }
  tr:nth-child(even) td { background: #fafbfc; }

  .transcript {
    margin-top: 40px; padding-top: 20px;
    border-top: 2px solid #e0e0e0;
  }
  .transcript h2 { color: #999; }
  .seg { margin: 2px 0; font-size: 13px; color: #444; }
  .ts { color: #999; font-size: 12px; font-family: monospace; }

  .footer {
    margin-top: 48px; padding-top: 16px;
    border-top: 1px solid #e0e0e0;
    color: #999; font-size: 11px; text-align: center;
  }

  @media print {
    body { padding: 20px; font-size: 12px; }
    .transcript { page-break-before: always; }
  }
</style>
</head>
<body>

<div class="header">
  <h1>{{TITLE}}</h1>
  <div class="meta">
    <span>📅 {{DATE}}</span>
    <span>⏱ {{DURATION}}</span>
    <span>📁 {{FILENAME}}</span>
  </div>
</div>

<div class="analysis">
{{ANALYSIS}}
</div>

<div class="transcript">
  <h2>Транскрипция</h2>
  {{TRANSCRIPT}}
</div>

<div class="footer">
  Сгенерировано VisionFlow — Meeting Intelligence
</div>

</body>
</html>"""
