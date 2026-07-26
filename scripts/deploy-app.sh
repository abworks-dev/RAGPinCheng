#!/usr/bin/env bash
# Deploy Ubuntu application node (backend + Qdrant).
# Called by GitHub Actions (deploy-app job).
#
# Prerequisites:
#   - Docker & Compose installed on the host
#   - Repository cloned at /opt/RAGPinCheng (or REPO_PATH env var)
#   - .env file configured at REPO_PATH/.env
#   - GPU service already running and healthy at GPU_SERVICE_URL

set -euo pipefail

REPO_PATH="${REPO_PATH:-/data/workspace/projects/ragpincheng}"
BACKUP_DIR="${BACKUP_DIR:-/data/backup/databases/ragpincheng}"
COMPOSE_FILE="${REPO_PATH}/docker/docker-compose.yml"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_PATH="${BACKUP_DIR}/app-backup-${TIMESTAMP}"

echo "========================================="
echo " Ubuntu App Deploy — ${TIMESTAMP}"
echo "========================================="

# ── 1. Check prerequisites ────────────────────────────────────────────────
echo ">> Checking prerequisites"
command -v docker >/dev/null 2>&1 || { echo "docker not found"; exit 1; }
[ -d "$REPO_PATH" ] || { echo "REPO_PATH not found: $REPO_PATH"; exit 1; }
[ -f "$COMPOSE_FILE" ] || { echo "Compose file not found: $COMPOSE_FILE"; exit 1; }

# ── 2. Verify GPU service contract ────────────────────────────────────────
echo ">> Checking GPU service contract"
GPU_URL="${GPU_SERVICE_URL:-http://192.168.11.11:8100}"
MODEL_INFO=$(curl -fsS "${GPU_URL}/model-info" 2>/dev/null || echo "")
if [ -z "$MODEL_INFO" ]; then
    echo "ERROR: GPU service unreachable at ${GPU_URL}"
    echo "Deploy aborted — ensure the GPU service is running first."
    exit 1
fi

API_VER=$(echo "$MODEL_INFO" | python3 -c "import sys,json; print(json.load(sys.stdin).get('api_version',''))" 2>/dev/null || echo "")
EMBED_DIM=$(echo "$MODEL_INFO" | python3 -c "import sys,json; print(json.load(sys.stdin).get('embedding_dimension',0))" 2>/dev/null || echo "0")

if [ "$API_VER" != "1" ]; then
    echo "ERROR: GPU service API version mismatch: expected 1, got ${API_VER:-unknown}"
    exit 1
fi
if [ "$EMBED_DIM" != "1024" ]; then
    echo "ERROR: GPU service embedding dimension mismatch: expected 1024, got ${EMBED_DIM}"
    exit 1
fi
echo "  GPU service OK (api=$API_VER, dim=$EMBED_DIM)"

# ── 3. Pull latest code ───────────────────────────────────────────────────
if [ "${SKIP_GIT_PULL:-0}" != "1" ]; then
    echo ">> Pulling latest code"
    cd "$REPO_PATH"
    git pull origin master
else
    echo ">> Skipping git pull (using checked-out code)"
fi

# ── 4. Backup current state ───────────────────────────────────────────────
echo ">> Backing up current state to ${BACKUP_PATH}"
mkdir -p "$BACKUP_DIR"
mkdir -p "$BACKUP_PATH"
# Backup SQLite databases
if [ -f "$REPO_PATH/data/app.sqlite" ]; then
    cp "$REPO_PATH/data/app.sqlite" "${BACKUP_PATH}/app.sqlite"
fi
if [ -f "$REPO_PATH/data/parents.sqlite" ]; then
    cp "$REPO_PATH/data/parents.sqlite" "${BACKUP_PATH}/parents.sqlite"
fi
# Record current Docker image hash
docker images pincheng-rag-backend:latest --format "{{.ID}}" > "${BACKUP_PATH}/image-hash.txt" 2>/dev/null || true

# ── 5. Validate Docker Compose configuration ──────────────────────────────
echo ">> Validating Compose configuration"
docker compose -f "$COMPOSE_FILE" config --quiet || {
    echo "ERROR: Invalid Compose configuration"
    exit 1
}

# ── 6. Build new backend image ────────────────────────────────────────────
echo ">> Building backend image"
docker compose -f "$COMPOSE_FILE" build backend 2>&1 | tail -5

# ── 7. Deploy (rolling update) ────────────────────────────────────────────
echo ">> Deploying services"
docker compose -f "$COMPOSE_FILE" up -d --no-deps backend 2>&1

# ── 8. Wait for health ────────────────────────────────────────────────────
echo ">> Waiting for backend health check"
for i in $(seq 1 12); do
    HEALTH=$(curl -fsS http://localhost/api/health 2>/dev/null || echo "")
    if echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if d.get('status')=='ok' else 1)" 2>/dev/null; then
        echo "  Backend healthy"
        break
    fi
    echo "  Waiting... attempt $i/12"
    sleep 5
done

# ── 9. Qdrant health check ────────────────────────────────────────────────
echo ">> Checking Qdrant"
docker compose -f "$COMPOSE_FILE" exec qdrant curl -fsS http://localhost:6333/collections/pincheng_docs \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  Qdrant OK: {d[\"result\"][\"points_count\"]} points')" \
    2>/dev/null || echo "  WARNING: Qdrant health check failed"

# ── 10. Final verification ────────────────────────────────────────────────
echo ">> Running end-to-end verification"
# Verify the backend can reach the GPU service
E2E_CHECK=$(curl -fsS http://localhost/api/config 2>/dev/null || echo "")
if [ -n "$E2E_CHECK" ]; then
    echo "  API config: $(echo "$E2E_CHECK" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'embed={d.get(\"embed_model\",\"?\")}')" 2>/dev/null || echo "unavailable")"
    echo "  Deploy successful"
else
    echo "  WARNING: API endpoint not responding"
fi

echo "========================================="
echo " Deploy complete"
echo "========================================="
exit 0