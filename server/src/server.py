"""VisionFlow — FastAPI server.

Endpoints:
  Meetings:
    POST /api/meetings/upload       — upload audio file, process async
    GET  /api/meetings              — list all meetings
    GET  /api/meetings/{id}         — meeting details + analysis
    GET  /api/meetings/{id}/report  — HTML report

  Communication Assistant:
    GET  /api/profiles              — list available profiles
    GET  /api/profiles/{id}/guide   — communication guide for person
    POST /api/communicate           — adapt message for recipient
    POST /api/communicate/transcribe — transcribe audio for context

  Pages:
    GET  /communicate               — communication assistant UI
    GET  /health                    — healthcheck
"""

import asyncio
import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, HTTPException, BackgroundTasks, Form
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from pydantic import BaseModel

from src import config
from src.transcriber import transcribe, format_transcript
from src.analyzer import analyze
from src.storage import (
    get_pool, close_pool, save_meeting, get_meeting,
    list_meetings, count_meetings, save_processing_status, update_meeting_status,
)
from src.report import generate_html_report
from src.communicator import adapt_message
from src.profiles import list_profiles, get_guide, setup_tables as setup_profile_tables

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("mi-server")

app = FastAPI(
    title="VisionFlow — Meeting Intelligence",
    version="1.0.0",
    description="Audio → Transcript → Analysis → Report",
)


@app.on_event("startup")
async def startup():
    os.makedirs(config.UPLOAD_DIR, exist_ok=True)
    await get_pool()
    await setup_profile_tables()
    log.info("VisionFlow server started on :%d", config.PORT)


@app.on_event("shutdown")
async def shutdown():
    await close_pool()


# ─── Health ──────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    checks = {"server": "ok"}
    try:
        pool = await get_pool()
        await pool.fetchval("SELECT 1")
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"

    checks["groq_api_key"] = "set" if config.GROQ_API_KEY else "missing"
    checks["anthropic_api_key"] = "set" if config.ANTHROPIC_API_KEY else "missing"

    status = 200 if all(v in ("ok", "set") for v in checks.values()) else 503
    return JSONResponse(checks, status_code=status)


# ─── Upload ─────────────────────────────────────────────────────────────────

@app.post("/api/meetings/upload")
async def upload_meeting(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    """Upload audio file for processing."""
    # Validate extension
    ext = Path(file.filename).suffix.lower()
    if ext not in config.AUDIO_EXTENSIONS:
        raise HTTPException(400, f"Unsupported format: {ext}. Supported: {', '.join(sorted(config.AUDIO_EXTENSIONS))}")

    # Save to temp
    upload_path = Path(config.UPLOAD_DIR) / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{file.filename}"
    with open(upload_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    file_size_mb = upload_path.stat().st_size / (1024 * 1024)
    if file_size_mb > config.UPLOAD_MAX_SIZE_MB:
        upload_path.unlink()
        raise HTTPException(400, f"File too large: {file_size_mb:.1f} MB (max {config.UPLOAD_MAX_SIZE_MB} MB)")

    log.info("Received upload: %s (%.1f MB)", file.filename, file_size_mb)

    # Create record with 'processing' status
    meeting_id = await save_processing_status(file.filename, "processing")

    # Process in background
    background_tasks.add_task(_process_meeting, meeting_id, str(upload_path), file.filename)

    return {
        "id": meeting_id,
        "status": "processing",
        "message": f"Файл {file.filename} принят. Обработка занимает 1-5 минут.",
    }


async def _process_meeting(meeting_id: int, audio_path: str, filename: str):
    """Background task: transcribe → analyze → save."""
    try:
        # Step 1: Transcribe
        log.info("[#%d] Step 1/3: Transcribing...", meeting_id)
        result = await transcribe(audio_path)

        # Step 2: Format transcript
        transcript_formatted = format_transcript(result["segments"])

        # Step 3: Analyze
        log.info("[#%d] Step 2/3: Analyzing...", meeting_id)
        analysis = await analyze(result["text"])

        # Step 4: Save to DB (update existing record)
        log.info("[#%d] Step 3/3: Saving...", meeting_id)
        pool = await get_pool()
        await pool.execute(
            """
            UPDATE mi_meetings SET
                duration_sec = $1, language = $2,
                transcript_text = $3, transcript_formatted = $4,
                analysis_text = $5, segments = $6,
                status = 'completed', error_message = NULL
            WHERE id = $7
            """,
            result["duration"],
            result["language"],
            result["text"],
            transcript_formatted,
            analysis,
            __import__("json").dumps(result["segments"], ensure_ascii=False),
            meeting_id,
        )

        log.info("[#%d] ✓ Complete: %s (%.0f sec)", meeting_id, filename, result["duration"])

    except Exception as e:
        log.error("[#%d] ✗ Error: %s", meeting_id, e)
        await update_meeting_status(meeting_id, "error", str(e))

    finally:
        # Cleanup uploaded file
        try:
            Path(audio_path).unlink(missing_ok=True)
        except Exception:
            pass


# ─── List meetings ──────────────────────────────────────────────────────────

@app.get("/api/meetings")
async def api_list_meetings(limit: int = 50, offset: int = 0):
    meetings = await list_meetings(limit, offset)
    total = await count_meetings()
    for m in meetings:
        if m.get("created_at"):
            m["created_at"] = m["created_at"].isoformat()
    return {"meetings": meetings, "total": total}


# ─── Meeting details ────────────────────────────────────────────────────────

@app.get("/api/meetings/{meeting_id}")
async def api_get_meeting(meeting_id: int):
    m = await get_meeting(meeting_id)
    if not m:
        raise HTTPException(404, "Meeting not found")
    if m.get("created_at"):
        m["created_at"] = m["created_at"].isoformat()
    return m


# ─── HTML report ────────────────────────────────────────────────────────────

@app.get("/api/meetings/{meeting_id}/report")
async def api_get_report(meeting_id: int):
    m = await get_meeting(meeting_id)
    if not m:
        raise HTTPException(404, "Meeting not found")
    if m["status"] != "completed":
        raise HTTPException(400, f"Meeting not ready: status={m['status']}")

    html = generate_html_report(
        filename=m["filename"],
        duration_sec=m["duration_sec"],
        transcript_formatted=m["transcript_formatted"],
        analysis_text=m["analysis_text"],
        created_at=m.get("created_at"),
    )
    return HTMLResponse(html)


# ═══════════════════════════════════════════════════════════════════════════
# COMMUNICATION ASSISTANT
# ═══════════════════════════════════════════════════════════════════════════

# ─── UI page ────────────────────────────────────────────────────────────────

@app.get("/communicate", response_class=HTMLResponse)
async def communicate_page():
    html_path = Path(config.TEMPLATES_DIR) / "communicate.html"
    if not html_path.exists():
        raise HTTPException(404, "Communication page not found")
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


# ─── Profiles ───────────────────────────────────────────────────────────────

@app.get("/api/profiles")
async def api_list_profiles(user_id: str | None = None):
    profiles = await list_profiles(user_id)
    return {"profiles": profiles}


@app.get("/api/profiles/{profile_id}/guide")
async def api_get_guide(profile_id: str, user_id: str | None = None):
    data = await get_guide(profile_id, user_id)
    if not data:
        raise HTTPException(404, "Profile not found or access denied")
    return data


# ─── Communicate ────────────────────────────────────────────────────────────

class CommunicateRequest(BaseModel):
    profile_id: str
    context: str
    task: str = "ответить на сообщение"
    goal: str = "respond"
    msg_type: str = "email"
    include_notes: bool = False


@app.post("/api/communicate")
async def api_communicate(req: CommunicateRequest):
    guide_data = await get_guide(req.profile_id)
    if not guide_data:
        raise HTTPException(404, f"Profile '{req.profile_id}' not found")

    guide_text = guide_data.get("guide", "")
    if not guide_text:
        raise HTTPException(400, f"No communication guide for '{req.profile_id}'")

    result = await adapt_message(
        guide=guide_text,
        context=req.context,
        task=req.task,
        goal=req.goal,
        msg_type=req.msg_type,
        include_notes=req.include_notes,
    )
    return result


# ─── Transcribe audio for context ───────────────────────────────────────────

@app.post("/api/communicate/transcribe")
async def api_transcribe_for_context(file: UploadFile = File(...)):
    """Transcribe audio file and return text for communication context."""
    ext = Path(file.filename).suffix.lower()
    if ext not in config.AUDIO_EXTENSIONS:
        raise HTTPException(400, f"Unsupported format: {ext}")

    upload_path = Path(config.UPLOAD_DIR) / f"ctx_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{file.filename}"
    with open(upload_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        result = await transcribe(str(upload_path))
        formatted = format_transcript(result["segments"])
        return {
            "transcript": formatted,
            "text": result["text"],
            "duration": result["duration"],
            "language": result["language"],
        }
    except Exception as e:
        raise HTTPException(500, f"Transcription error: {e}")
    finally:
        upload_path.unlink(missing_ok=True)


# ─── Entry point ────────────────────────────────────────────────────────────

def main():
    import uvicorn
    uvicorn.run(
        "src.server:app",
        host=config.HOST,
        port=config.PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()
