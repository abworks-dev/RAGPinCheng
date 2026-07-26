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

REPO_PATH="${REPO_PATH:-${PRODUCTION_APP_REPO_PATH}}"
BACKUP_DIR="${BACKUP_DIR:-${PRODUCTION_APP_BACKUP_DIRECTORY}}"
DATA_PATH="${DATA_PATH:-${PRODUCTION_APP_DATA_PATH}}"
COMPOSE_BASE="${REPO_PATH}/docker/docker-compose.yml"
COMPOSE_OVERRIDE="/data/services/docker/compose/ragpincheng/prod/compose.prod.yaml"
COMPOSE_ENV_FILE="${PRODUCTION_APP_ENV_FILE}"
COMPOSE_PROJECT="ragpincheng-prod"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_PATH="${BACKUP_DIR}/app-backup-${TIMESTAMP}"

# Helper: build the full compose command with project, files, and env file.
compose() {
    docker compose -p "$COMPOSE_PROJECT" \
        -f "$COMPOSE_BASE" \
        -f "$COMPOSE_OVERRIDE" \
        --env-file "$COMPOSE_ENV_FILE" \
        "$@"
}

echo "========================================="
echo " Ubuntu App Deploy — ${TIMESTAMP}"
echo "========================================="

# ── 1. Check prerequisites ────────────────────────────────────────────────
echo ">> Checking prerequisites"
command -v docker >/dev/null 2>&1 || { echo "docker not found"; exit 1; }
[ -d "$REPO_PATH" ] || { echo "REPO_PATH not found: $REPO_PATH"; exit 1; }
[ -f "$COMPOSE_BASE" ] || { echo "Compose file not found: $COMPOSE_BASE"; exit 1; }

# ── 2. Verify GPU service contract ────────────────────────────────────────
echo ">> Checking GPU service contract"
GPU_URL="${GPU_SERVICE_URL:-http://${PRIVATE_IPV4}:8100}"
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
echo ">> Pulling latest code"
cd "$REPO_PATH"
# Use GitHub Actions token for authentication
if [ -n "${GIT_TOKEN:-}" ]; then
    git remote set-url origin "https://x-access-token:${GIT_TOKEN}@github.com/abworks-dev/RAGPinCheng.git"
fi
git pull origin master

# ── 4. Backup current state ───────────────────────────────────────────────
echo ">> Backing up current state to ${BACKUP_PATH}"
mkdir -p "$BACKUP_DIR"
mkdir -p "$BACKUP_PATH"
# Backup SQLite databases
if [ -f "$DATA_PATH/app.sqlite" ]; then
    cp "$DATA_PATH/app.sqlite" "${BACKUP_PATH}/app.sqlite"
fi
if [ -f "$DATA_PATH/parents.sqlite" ]; then
    cp "$DATA_PATH/parents.sqlite" "${BACKUP_PATH}/parents.sqlite"
fi
# Record current Docker image hash
docker images pincheng-rag-backend:latest --format "{{.ID}}" > "${BACKUP_PATH}/image-hash.txt" 2>/dev/null || true

# ── 5. Validate Docker Compose configuration ──────────────────────────────
echo ">> Validating Compose configuration"
compose config --quiet || {
    echo "ERROR: Invalid Compose configuration"
    exit 1
}

# ── 6. Build new backend image ────────────────────────────────────────────
echo ">> Building backend image"
compose build backend 2>&1 | tail -5

# ── 7. Deploy (rolling update) ────────────────────────────────────────────
echo ">> Deploying services"
compose up -d --no-deps backend 2>&1

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
compose exec qdrant curl -fsS http://localhost:6333/collections/pincheng_docs \
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