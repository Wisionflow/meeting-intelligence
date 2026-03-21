# Meeting Intelligence — Server

Серверная версия Meeting Intelligence для деплоя на Algora инфраструктуру.

**Архитектура:**
- Groq Whisper API (облачная транскрипция, без GPU)
- Claude API (анализ)
- PostgreSQL (хранение)
- FastAPI (веб-сервер)
- HTML-отчёты (самодостаточные, без зависимостей)

## Quick Start

```bash
# 1. Настроить
cp .env.example .env
# заполнить GROQ_API_KEY, ANTHROPIC_API_KEY

# 2. Создать таблицы
docker compose run --rm meetings python scripts/setup_db.py

# 3. Проверить
docker compose run --rm meetings python scripts/check.py

# 4. Запустить
docker compose up -d

# 5. Проверить
curl http://127.0.0.1:8080/health
```

## API

### Upload audio
```bash
curl -X POST http://127.0.0.1:8080/api/meetings/upload \
  -F "file=@meeting.m4a"

# Response:
# {"id": 1, "status": "processing", "message": "..."}
```

### List meetings
```bash
curl http://127.0.0.1:8080/api/meetings
```

### Get meeting details
```bash
curl http://127.0.0.1:8080/api/meetings/1
```

### Get HTML report
```bash
curl http://127.0.0.1:8080/api/meetings/1/report > report.html
# or open in browser: https://cmo.algora.media/api/meetings/1/report
```

## Deploy on Algora Server

```bash
ssh mantas@167.17.181.140
cd /home/mantas/cmo/

# Copy server package
# (git pull or scp)

cp server/.env.example server/.env
# edit server/.env

cd server
DOCKER_BUILDKIT=0 docker build -t algora_cmo_meetings .
docker run --rm --env-file .env --network host algora_cmo_meetings python scripts/setup_db.py
docker run --rm --env-file .env --network host algora_cmo_meetings python scripts/check.py
docker compose up -d

# Verify
curl http://127.0.0.1:8080/health
# Web: https://cmo.algora.media/api/meetings
```

## File Structure

```
server/
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── requirements.txt
├── README.md
├── src/
│   ├── config.py          ← settings from .env
│   ├── server.py          ← FastAPI (upload, list, report)
│   ├── transcriber.py     ← Groq Whisper API
│   ├── analyzer.py        ← Claude API analysis
│   ├── storage.py         ← PostgreSQL queries
│   └── report.py          ← HTML report generator
├── scripts/
│   ├── setup_db.py        ← create tables
│   └── check.py           ← verify configuration
├── prompts/
│   └── meeting_analysis.txt  ← editable analysis prompt
└── templates/
    └── (report.html)      ← optional custom template
```
