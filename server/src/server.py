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

from fastapi import FastAPI, File, UploadFile, HTTPException, BackgroundTasks, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse
from pydantic import BaseModel

from src import config
from src.auth import get_current_user, check_credentials, create_session_cookie, COOKIE_NAME
from src.transcriber import transcribe, format_transcript
from src.analyzer import analyze
from src.storage import (
    get_pool, close_pool, save_meeting, get_meeting,
    list_meetings, count_meetings, save_processing_status, update_meeting_status,
)
from src.report import generate_html_report
from src.communicator import adapt_message, strategic_analysis, chat_analysis, rewrite_tone
from src.profiles import (
    list_profiles, get_guide, get_full_profile,
    setup_tables as setup_profile_tables,
)

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
    pool = await get_pool()
    await setup_profile_tables()
    # Create communications table if missing
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS mi_communications (
            id              SERIAL PRIMARY KEY,
            user_id         TEXT NOT NULL DEFAULT '',
            profile_id      TEXT DEFAULT '',
            profile_name    TEXT DEFAULT '',
            context         TEXT DEFAULT '',
            context_type    TEXT DEFAULT '',
            task            TEXT DEFAULT '',
            goal            TEXT DEFAULT '',
            msg_type        TEXT DEFAULT '',
            generated_message TEXT DEFAULT '',
            notes           TEXT DEFAULT '',
            tokens_input    INT DEFAULT 0,
            tokens_output   INT DEFAULT 0,
            analysis_json   TEXT DEFAULT '',
            mode            TEXT DEFAULT 'simple',
            session_id      TEXT DEFAULT '',
            created_at      TIMESTAMPTZ DEFAULT NOW()
        )
    """)
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


# ─── Auth ───────────────────────────────────────────────────────────────────

def _require_user(request: Request) -> str:
    """Extract user_id from session cookie or raise 401/redirect."""
    user_id = get_current_user(request)
    if not user_id:
        raise HTTPException(401, "Not authenticated")
    return user_id


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    html_path = Path(config.TEMPLATES_DIR) / "login.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.post("/login")
async def login_submit(username: str = Form(...), password: str = Form(...)):
    if check_credentials(username, password):
        resp = RedirectResponse("/communicate", status_code=303)
        create_session_cookie(resp, username)
        log.info("Login OK: %s", username)
        return resp
    log.warning("Login FAILED: %s", username)
    return RedirectResponse("/login?error=1", status_code=303)


@app.get("/logout")
async def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    return resp


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
async def communicate_page(request: Request):
    user_id = get_current_user(request)
    if not user_id:
        return RedirectResponse("/login", status_code=303)
    html_path = Path(config.TEMPLATES_DIR) / "communicate.html"
    if not html_path.exists():
        raise HTTPException(404, "Communication page not found")
    # Inject user_id into page so JS can use it
    html = html_path.read_text(encoding="utf-8")
    html = html.replace("</head>", f'<script>window.__USER_ID = "{user_id}";</script>\n</head>')
    return HTMLResponse(html)


# ─── Profiles ───────────────────────────────────────────────────────────────

@app.get("/api/profiles")
async def api_list_profiles(request: Request):
    user_id = _require_user(request)
    profiles = await list_profiles(user_id)
    return {"profiles": profiles}


@app.get("/api/profiles/{profile_id}/guide")
async def api_get_guide(profile_id: str, request: Request):
    user_id = _require_user(request)
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
    user_id: str = ""
    context_type: str = "text"  # text | audio
    audio_filename: str = ""
    mode: str = "strategic"  # "simple" (v1) or "strategic" (v2, 5 blocks)
    sender_id: str = ""  # optional: sender profile for strategic mode


async def _detect_third_parties(
    context: str, recipient_id: str, sender_id: str = "",
) -> list[dict]:
    """Detect mentioned people in context and load their profiles."""
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT id, display_name, guide, profile_data FROM mi_profiles"
    )
    third_parties = []
    for r in rows:
        pid = r["id"]
        if pid == recipient_id or pid == sender_id:
            continue
        name = r["display_name"] or ""
        # Check if any part of the name appears in context
        name_parts = name.split()
        if any(part for part in name_parts if len(part) > 3 and part in context):
            third_parties.append({
                "name": name,
                "profile": r["profile_data"] or "",
                "guide": r["guide"] or "",
            })
    return third_parties[:3]  # max 3 third parties


@app.post("/api/communicate")
async def api_communicate(req: CommunicateRequest, request: Request):
    user_id = _require_user(request)
    req.user_id = user_id
    guide_data = await get_guide(req.profile_id, user_id)
    if not guide_data:
        raise HTTPException(404, f"Profile '{req.profile_id}' not found")

    guide_text = guide_data.get("guide", "")
    if not guide_text:
        raise HTTPException(400, f"No communication guide for '{req.profile_id}'")

    if req.mode == "strategic":
        # V2: full 5-block strategic analysis
        full_profile = await get_full_profile(req.profile_id)
        recipient_profile = full_profile.get("profile_data", "") if full_profile else ""

        # Load sender profile if provided
        sender_profile = None
        if req.sender_id:
            sp = await get_full_profile(req.sender_id)
            if sp:
                sender_profile = sp.get("profile_data", "")

        # Detect third parties mentioned in context
        third_parties = await _detect_third_parties(
            req.context, req.profile_id, req.sender_id,
        )

        result = await strategic_analysis(
            recipient_guide=guide_text,
            recipient_profile=recipient_profile,
            context=req.context,
            task=req.task,
            goal=req.goal,
            msg_type=req.msg_type,
            sender_profile=sender_profile,
            third_parties=third_parties if third_parties else None,
        )

        # Save to DB
        pool = await get_pool()
        row = await pool.fetchrow(
            """
            INSERT INTO mi_communications
                (user_id, profile_id, profile_name, context, context_type,
                 audio_filename, task, goal, msg_type,
                 generated_message, notes, tokens_input, tokens_output,
                 analysis_json, mode)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
            RETURNING id
            """,
            req.user_id,
            req.profile_id,
            guide_data.get("display_name", ""),
            req.context[:10000],
            req.context_type,
            req.audio_filename or None,
            req.task,
            req.goal,
            req.msg_type,
            result.get("message", ""),
            None,
            result.get("tokens", {}).get("input", 0),
            result.get("tokens", {}).get("output", 0),
            json.dumps(result.get("blocks", {}), ensure_ascii=False),
            "strategic",
        )
        result["id"] = row["id"]
        log.info(
            "Strategic comm #%d: %s → %s, third_parties=%s",
            row["id"], req.user_id or "anon", req.profile_id,
            result.get("third_parties_used", []),
        )
        return result

    else:
        # V1: simple adaptation
        result = await adapt_message(
            guide=guide_text,
            context=req.context,
            task=req.task,
            goal=req.goal,
            msg_type=req.msg_type,
            include_notes=req.include_notes,
        )

        pool = await get_pool()
        row = await pool.fetchrow(
            """
            INSERT INTO mi_communications
                (user_id, profile_id, profile_name, context, context_type,
                 audio_filename, task, goal, msg_type,
                 generated_message, notes, tokens_input, tokens_output,
                 mode)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
            RETURNING id
            """,
            req.user_id,
            req.profile_id,
            guide_data.get("display_name", ""),
            req.context[:10000],
            req.context_type,
            req.audio_filename or None,
            req.task,
            req.goal,
            req.msg_type,
            result.get("message", ""),
            result.get("notes"),
            result.get("tokens", {}).get("input", 0),
            result.get("tokens", {}).get("output", 0),
            "simple",
        )
        result["id"] = row["id"]
        log.info("Simple comm #%d: %s → %s", row["id"], req.user_id or "anon", req.profile_id)
        return result


# ─── Communication history ──────────────────────────────────────────────

@app.get("/api/communications")
async def api_list_communications(request: Request, limit: int = 30, offset: int = 0):
    user_id = _require_user(request)
    pool = await get_pool()
    if user_id:
        rows = await pool.fetch(
            """
            SELECT id, profile_id, profile_name, task, goal,
                   LEFT(generated_message, 100) as preview,
                   context_type, mode, created_at
            FROM mi_communications
            WHERE user_id = $1
            ORDER BY created_at DESC
            LIMIT $2 OFFSET $3
            """,
            user_id, limit, offset,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT id, user_id, profile_id, profile_name, task, goal,
                   LEFT(generated_message, 100) as preview,
                   context_type, mode, created_at
            FROM mi_communications
            ORDER BY created_at DESC
            LIMIT $1 OFFSET $2
            """,
            limit, offset,
        )

    items = []
    for r in rows:
        d = dict(r)
        if d.get("created_at"):
            d["created_at"] = d["created_at"].isoformat()
        items.append(d)
    return {"communications": items}


@app.get("/api/communications/{comm_id}")
async def api_get_communication(comm_id: int):
    pool = await get_pool()
    row = await pool.fetchrow("SELECT * FROM mi_communications WHERE id = $1", comm_id)
    if not row:
        raise HTTPException(404, "Communication not found")
    d = dict(row)
    if d.get("created_at"):
        d["created_at"] = d["created_at"].isoformat()
    # Parse analysis_json back to dict for strategic mode
    if d.get("analysis_json"):
        try:
            d["blocks"] = json.loads(d["analysis_json"])
        except (json.JSONDecodeError, TypeError):
            d["blocks"] = {}
    return d


# ─── Transcribe audio for context ───────────────────────────────────────────

@app.post("/api/communicate/transcribe")
async def api_transcribe_for_context(request: Request, file: UploadFile = File(...)):
    _require_user(request)
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


# ─── Chunked upload (bypass nginx 1MB limit) ─────────────────────────────────

_chunked_uploads: dict[str, dict] = {}  # upload_id -> {path, filename, received}


@app.post("/api/communicate/upload-chunk")
async def api_upload_chunk(request: Request, upload_id: str = "", chunk_index: int = 0,
                           total_chunks: int = 1, filename: str = "",
                           file: UploadFile = File(...)):
    _require_user(request)
    if not upload_id:
        raise HTTPException(400, "upload_id required")

    if upload_id not in _chunked_uploads:
        upload_path = Path(config.UPLOAD_DIR) / f"chunked_{upload_id}"
        upload_path.mkdir(parents=True, exist_ok=True)
        _chunked_uploads[upload_id] = {
            "dir": upload_path, "filename": filename,
            "total": total_chunks, "received": set(),
        }

    info = _chunked_uploads[upload_id]
    chunk_path = info["dir"] / f"chunk_{chunk_index:04d}"
    with open(chunk_path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    info["received"].add(chunk_index)

    return {"ok": True, "received": len(info["received"]), "total": info["total"]}


@app.post("/api/communicate/upload-complete")
async def api_upload_complete(request: Request, upload_id: str = ""):
    _require_user(request)
    if upload_id not in _chunked_uploads:
        raise HTTPException(404, "Upload not found")

    info = _chunked_uploads.pop(upload_id)
    if len(info["received"]) < info["total"]:
        raise HTTPException(400, f"Missing chunks: got {len(info['received'])}/{info['total']}")

    # Assemble file
    assembled = Path(config.UPLOAD_DIR) / f"ctx_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{info['filename']}"
    with open(assembled, "wb") as out:
        for i in range(info["total"]):
            chunk_path = info["dir"] / f"chunk_{i:04d}"
            with open(chunk_path, "rb") as cp:
                shutil.copyfileobj(cp, out)

    # Cleanup chunks
    shutil.rmtree(info["dir"], ignore_errors=True)

    # Transcribe
    try:
        result = await transcribe(str(assembled))
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
        assembled.unlink(missing_ok=True)


# ─── Upload file for context (images, documents) ─────────────────────────────

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
DOC_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".xml", ".html"}


@app.post("/api/communicate/upload-file")
async def api_upload_context_file(request: Request, file: UploadFile = File(...)):
    """Upload image or document to use as communication context.

    Images: saved and returned as base64 data URL for Claude vision.
    Documents: text extracted and returned.
    """
    _require_user(request)
    import base64

    ext = Path(file.filename).suffix.lower()
    content = await file.read()

    if ext in IMAGE_EXTENSIONS:
        # Return base64 for frontend to include in context
        b64 = base64.b64encode(content).decode()
        mime = {
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
        }.get(ext, "image/png")
        return {
            "type": "image",
            "filename": file.filename,
            "data_url": f"data:{mime};base64,{b64}",
            "size": len(content),
        }

    elif ext in DOC_EXTENSIONS:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = content.decode("cp1251", errors="replace")
        return {
            "type": "document",
            "filename": file.filename,
            "text": text[:20000],
            "size": len(content),
        }

    else:
        raise HTTPException(
            400,
            f"Unsupported format: {ext}. "
            f"Supported: {', '.join(sorted(IMAGE_EXTENSIONS | DOC_EXTENSIONS | config.AUDIO_EXTENSIONS))}",
        )


# ─── Chat endpoint (v3 — natural language) ────────────────────────────────

class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str

class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = []
    session_id: str = ""
    user_id: str = ""


async def _detect_profiles_in_text(text: str) -> list[dict]:
    """Find all known people mentioned in text and load their full profiles.

    Uses stem matching (first N chars of surname) to handle Russian declensions:
    Хренова/Хреновой/Хренову, Попов/Попова/Попову etc.
    """
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT id, display_name, guide, profile_data FROM mi_profiles"
    )
    detected = []
    text_lower = text.lower()
    for r in rows:
        name = r["display_name"] or ""
        parts = name.split()
        for part in parts:
            if len(part) < 4:
                continue
            part_lower = part.lower()
            # Stem: for short names (4-5 chars) use full word,
            # for longer names trim last 2 chars (min 4) to handle declensions
            if len(part_lower) <= 5:
                stem = part_lower
            else:
                stem = part_lower[:max(4, len(part_lower) - 2)]
            if stem in text_lower:
                detected.append({
                    "id": r["id"],
                    "name": name,
                    "profile": r["profile_data"] or "",
                    "guide": r["guide"] or "",
                })
                log.info("Profile detected: '%s' (stem='%s') in text", name, stem)
                break
    return detected[:5]  # max 5 profiles


@app.post("/api/communicate/chat")
async def api_chat(req: ChatRequest, request: Request):
    """Chat-style communication analysis. User writes naturally."""
    user_id = _require_user(request)
    req.user_id = user_id

    # Detect profiles in current message + recent history
    search_text = req.message
    for h in req.history[-4:]:
        if h.role == "user":
            search_text += " " + h.content

    detected = await _detect_profiles_in_text(search_text)

    result = await chat_analysis(
        message=req.message,
        history=[{"role": h.role, "content": h.content} for h in req.history],
        profiles=detected,
    )

    # Save to DB
    pool = await get_pool()
    blocks_json = json.dumps(result["blocks"], ensure_ascii=False) if result["blocks"] else ""
    row = await pool.fetchrow(
        """
        INSERT INTO mi_communications
            (user_id, profile_id, profile_name, context, context_type,
             task, goal, msg_type,
             generated_message, notes, tokens_input, tokens_output,
             analysis_json, mode, session_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
        RETURNING id
        """,
        req.user_id,
        detected[0]["id"] if detected else "",
        ", ".join(p["name"] for p in detected) if detected else "",
        req.message[:10000],
        "chat",
        req.message[:200],
        "chat",
        "email",
        result.get("text", "") or result.get("blocks", {}).get("message", ""),
        None,
        result.get("tokens", {}).get("input", 0),
        result.get("tokens", {}).get("output", 0),
        blocks_json,
        "chat",
        req.session_id,
    )
    result["id"] = row["id"]

    log.info(
        "Chat #%d: session=%s, profiles=%s, has_blocks=%s",
        row["id"], req.session_id[:8] if req.session_id else "none",
        [p["name"] for p in detected],
        bool(result.get("blocks")),
    )

    return result


@app.get("/api/communicate/sessions")
async def api_list_sessions(request: Request, limit: int = 20):
    user_id = _require_user(request)
    """List chat sessions for sidebar."""
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT session_id,
               MIN(context) as first_message,
               MAX(created_at) as last_activity,
               COUNT(*) as message_count
        FROM mi_communications
        WHERE mode = 'chat' AND session_id != ''
              AND ($1 = '' OR user_id = $1)
        GROUP BY session_id
        ORDER BY last_activity DESC
        LIMIT $2
        """,
        user_id, limit,
    )
    sessions = []
    for r in rows:
        d = dict(r)
        if d.get("last_activity"):
            d["last_activity"] = d["last_activity"].isoformat()
        # Trim first message for preview
        fm = d.get("first_message", "") or ""
        d["preview"] = fm[:80] + ("..." if len(fm) > 80 else "")
        sessions.append(d)
    return {"sessions": sessions}


# ─── Tone rewrite endpoint ────────────────────────────────────────────────

class ToneRequest(BaseModel):
    current_text: str
    tone: str  # softer | harder | formal | shorter
    context_summary: str = ""
    session_id: str = ""


@app.post("/api/communicate/tone")
async def api_tone(req: ToneRequest, request: Request):
    _require_user(request)
    """Rewrite Block 3 text with a different tone."""
    if req.tone not in ("softer", "harder", "formal", "shorter"):
        raise HTTPException(400, f"Invalid tone: {req.tone}. Use: softer, harder, formal, shorter")

    result = await rewrite_tone(
        current_text=req.current_text,
        tone=req.tone,
        context_summary=req.context_summary,
    )
    return result


# ─── Recipient profile lookup ─────────────────────────────────────────────

@app.get("/api/communicate/profile-card/{profile_id}")
async def api_profile_card(profile_id: str, request: Request):
    _require_user(request)
    """Get mini-card data for recipient display."""
    data = await get_full_profile(profile_id)
    if not data:
        raise HTTPException(404, "Profile not found")

    # Extract DISC type and key traits from profile_data
    profile_text = data.get("profile_data", "")
    disc_type = ""
    key_trait = ""

    # Parse DISC type
    import re
    m = re.search(r"Основной DISC-тип:\*?\*?\s*(.+?)(?:\n|$)", profile_text)
    if m:
        disc_type = m.group(1).strip().split("—")[0].strip()

    # Parse one-liner description
    m2 = re.search(r"Основной DISC-тип:.*?—\s*(.+?)(?:\n|$)", profile_text)
    if m2:
        key_trait = m2.group(1).strip()[:80]

    return {
        "id": data["id"],
        "display_name": data.get("display_name", ""),
        "role": data.get("role", ""),
        "department": data.get("department", ""),
        "disc_type": disc_type,
        "key_trait": key_trait,
    }


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
