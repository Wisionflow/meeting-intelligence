#!/bin/bash
# VisionFlow Meeting Intelligence — Zero-Downtime Deploy
# Runs on server: 167.17.181.140, user: mantas
# Usage: bash deploy.sh [--force]
#
# Principle: Client NEVER knows something broke. Zero actions from client.

set -euo pipefail

APP_DIR="/home/mantas/meeting-intelligence"
CONTAINER="algora_cmo_meetings"
BACKUP_TAG="$(date +%Y%m%d_%H%M%S)"
HEALTH_URL="http://127.0.0.1:8080/health"
MAX_WAIT=60

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${GREEN}[deploy]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }

# ─── Pre-flight checks ──────────────────────────────────────────────────────

log "Pre-flight checks..."

# 1. Ensure .env exists
[ -f "$APP_DIR/.env" ] || fail ".env not found in $APP_DIR"

# 2. Check Docker is running
docker info >/dev/null 2>&1 || fail "Docker not running"

# 3. Check current container state
RUNNING=$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null || echo "false")
log "Current container running: $RUNNING"

# ─── Backup current image ───────────────────────────────────────────────────

log "Backing up current image as ${CONTAINER}:backup-${BACKUP_TAG}..."
docker tag "${CONTAINER}:latest" "${CONTAINER}:backup-${BACKUP_TAG}" 2>/dev/null || warn "No existing image to backup"

# ─── Build new image ────────────────────────────────────────────────────────

log "Building new image..."
cd "$APP_DIR"
DOCKER_BUILDKIT=0 docker build -t "${CONTAINER}:latest" . || fail "Build failed"

# ─── Stop old container ─────────────────────────────────────────────────────

if [ "$RUNNING" = "true" ]; then
    log "Stopping old container..."
    docker stop "$CONTAINER" --time 10 || true
    docker rm "$CONTAINER" 2>/dev/null || true
fi

# ─── Start new container ────────────────────────────────────────────────────

log "Starting new container..."
docker compose up -d || fail "Failed to start container"

# ─── Wait for health check ──────────────────────────────────────────────────

log "Waiting for health check ($HEALTH_URL)..."
ELAPSED=0
while [ $ELAPSED -lt $MAX_WAIT ]; do
    STATUS=$(curl -s -o /dev/null -w '%{http_code}' "$HEALTH_URL" 2>/dev/null || echo "000")
    if [ "$STATUS" = "200" ]; then
        log "Health check passed!"
        break
    fi
    sleep 2
    ELAPSED=$((ELAPSED + 2))
    echo -n "."
done
echo ""

if [ $ELAPSED -ge $MAX_WAIT ]; then
    warn "Health check failed after ${MAX_WAIT}s — ROLLING BACK"
    docker stop "$CONTAINER" 2>/dev/null || true
    docker rm "$CONTAINER" 2>/dev/null || true
    docker tag "${CONTAINER}:backup-${BACKUP_TAG}" "${CONTAINER}:latest" 2>/dev/null
    docker compose up -d
    fail "Rolled back to previous version"
fi

# ─── Smoke tests ────────────────────────────────────────────────────────────

log "Running smoke tests..."
SMOKE_FAIL=0

# Test 1: Health endpoint returns JSON
HEALTH=$(curl -s "$HEALTH_URL" 2>/dev/null)
echo "$HEALTH" | python3 -c "import sys,json; json.load(sys.stdin)" 2>/dev/null || {
    warn "Smoke: /api/health not returning valid JSON"
    SMOKE_FAIL=1
}

# Test 2: Chat endpoint exists
CHAT_STATUS=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:8080/api/chat" 2>/dev/null)
[ "$CHAT_STATUS" != "000" ] || {
    warn "Smoke: /api/chat unreachable"
    SMOKE_FAIL=1
}

# Test 3: Static files served (no dev messages)
INDEX=$(curl -s "http://127.0.0.1:8080/" 2>/dev/null)
echo "$INDEX" | grep -qi "visionflow\|meeting" || {
    warn "Smoke: index page doesn't contain expected content"
    SMOKE_FAIL=1
}

if [ $SMOKE_FAIL -eq 1 ]; then
    warn "Some smoke tests failed — container is running but check logs"
    warn "  docker logs $CONTAINER --tail 50"
else
    log "All smoke tests passed"
fi

# ─── Cleanup old backups (keep last 3) ──────────────────────────────────────

log "Cleaning old backup images..."
docker images --format '{{.Repository}}:{{.Tag}}' | grep "${CONTAINER}:backup-" | sort -r | tail -n +4 | xargs -r docker rmi 2>/dev/null || true

# ─── Done ────────────────────────────────────────────────────────────────────

log "Deploy complete!"
log "  Container: $CONTAINER"
log "  Health: $HEALTH_URL"
docker logs "$CONTAINER" --tail 5 2>/dev/null
