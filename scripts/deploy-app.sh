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

REPO_PATH="${REPO_PATH:?REPO_PATH must be provided by the private deployment environment}"
BACKUP_DIR="${BACKUP_DIR:?BACKUP_DIR must be provided by the private deployment environment}"
DATA_PATH="${DATA_PATH:?DATA_PATH must be provided by the private deployment environment}"
COMPOSE_BASE="${REPO_PATH}/docker/docker-compose.yml"
COMPOSE_OVERRIDE="${COMPOSE_OVERRIDE:?COMPOSE_OVERRIDE must be provided by the private deployment environment}"
ORIGINAL_COMPOSE_OVERRIDE="${COMPOSE_OVERRIDE}"
COMPOSE_SOURCE_DECOUPLED="${REPO_PATH}/docker/compose.source-decoupled.yml"
COMPOSE_ENV_FILE="${COMPOSE_ENV_FILE:?COMPOSE_ENV_FILE must be provided by the private deployment environment}"
export COMPOSE_ENV_FILE
COMPOSE_PROJECT="ragpincheng-prod"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_PATH="${BACKUP_DIR}/app-backup-${TIMESTAMP}"
DEPLOY_COMMIT_SHA="${DEPLOY_COMMIT_SHA:?DEPLOY_COMMIT_SHA is required}"
SCHEMA_MIGRATION_ACTION="${SCHEMA_MIGRATION_ACTION:-BLOCK_PENDING}"

case "${SCHEMA_MIGRATION_ACTION}" in
    BLOCK_PENDING|APPLY_PENDING) ;;
    *)
        echo "ERROR: SCHEMA_MIGRATION_ACTION must be BLOCK_PENDING or APPLY_PENDING"
        exit 1
        ;;
esac

if [[ ! "$DEPLOY_COMMIT_SHA" =~ ^[0-9a-fA-F]{40}$ ]]; then
    echo "ERROR: DEPLOY_COMMIT_SHA must be a full 40-character commit SHA"
    exit 1
fi

git_fetch_exact_commit() {
    local commit_sha="$1"
    local basic_auth attempt delay
    local -a proxy_args=()
    : "${GIT_TOKEN:?GIT_TOKEN is required to fetch the approved commit}"
    basic_auth="$(printf 'x-access-token:%s' "$GIT_TOKEN" | base64 | tr -d '\n')"
    if [ -n "${DEPLOY_HTTP_PROXY:-}" ]; then
        proxy_args=(-c "http.proxy=${DEPLOY_HTTP_PROXY}")
    fi
    for attempt in 1 2 3 4; do
        if git -c http.version=HTTP/1.1 "${proxy_args[@]}" \
            -c "http.extraHeader=AUTHORIZATION: basic ${basic_auth}" fetch \
            https://github.com/abworks-dev/RAGPinCheng.git "$commit_sha"; then
            return 0
        fi
        if [ "$attempt" -eq 4 ]; then
            echo "ERROR: git fetch failed after 4 attempts"
            return 1
        fi
        delay=$((2 ** attempt))
        echo "Git fetch attempt ${attempt}/4 failed; retrying in ${delay}s"
        sleep "$delay"
    done
}

sanitize_source_decoupled_override() {
    local sanitized
    sanitized="${BACKUP_DIR}/.compose-private-source-decoupled-${TIMESTAMP}.json"
    mkdir -p "$BACKUP_DIR"
    docker compose -f "$COMPOSE_BASE" -f "$COMPOSE_OVERRIDE" \
        --env-file "$COMPOSE_ENV_FILE" \
        config --no-interpolate --no-env-resolution --no-consistency --format json \
        | python3 "${REPO_PATH}/scripts/sanitize_source_decoupled_override.py" \
            > "${sanitized}.tmp"
    mv "${sanitized}.tmp" "$sanitized"
    COMPOSE_OVERRIDE="$sanitized"
    SOURCE_DECOUPLED_OVERRIDE_SANITIZED=true
    export COMPOSE_OVERRIDE SOURCE_DECOUPLED_OVERRIDE_SANITIZED
}

if [ "${SOURCE_DECOUPLING_COMPLETE:-false}" = "true" ] && \
   [ "${SOURCE_DECOUPLED_OVERRIDE_SANITIZED:-false}" != "true" ]; then
    sanitize_source_decoupled_override
fi

COMPOSE_ARGS=(
    -p "$COMPOSE_PROJECT"
)
case "${SOURCE_DECOUPLING_COMPLETE:-false}" in
    true)
        [ "${SOURCE_DECOUPLED_OVERRIDE_SANITIZED:-false}" = "true" ] || {
            echo "ERROR: source-decoupled Compose configuration was not sanitized"
            exit 1
        }
        [ -f "$COMPOSE_SOURCE_DECOUPLED" ] || {
            echo "ERROR: source-decoupled Compose overlay is missing: ${COMPOSE_SOURCE_DECOUPLED}"
            exit 1
        }
        # The sanitized file is a complete normalized stack. Apply the
        # source-decoupled overlay last so Docker receives the explicit
        # volumes override and service-level tmpfs contract.
        COMPOSE_ARGS+=(-f "$COMPOSE_OVERRIDE")
        COMPOSE_ARGS+=(-f "$COMPOSE_SOURCE_DECOUPLED")
        ;;
    false|"")
        COMPOSE_ARGS+=(-f "$COMPOSE_BASE" -f "$COMPOSE_OVERRIDE")
        ;;
    *)
        echo "ERROR: SOURCE_DECOUPLING_COMPLETE must be true or false"
        exit 1
        ;;
esac
COMPOSE_ARGS+=(--env-file "$COMPOSE_ENV_FILE")

# Helper: build the full compose command with the final source-decoupling overlay.
compose() {
    docker compose "${COMPOSE_ARGS[@]}" "$@"
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
GPU_URL="${GPU_SERVICE_URL:?GPU_SERVICE_URL must be provided by the private deployment environment}"
GPU_HEALTH=$(curl -fsS "${GPU_URL}/health" 2>/dev/null || echo "")
if ! echo "$GPU_HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('status') == 'ok' and d.get('model_loaded') is True" 2>/dev/null; then
    echo "ERROR: GPU service is not healthy at ${GPU_URL}"
    exit 1
fi
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

# ── 3. Synchronize exact approved commit ─────────────────────────────────
echo ">> Synchronizing exact commit ${DEPLOY_COMMIT_SHA}"
cd "$REPO_PATH"
CURRENT_HEAD="$(git rev-parse HEAD)"
if [ "$CURRENT_HEAD" != "$DEPLOY_COMMIT_SHA" ]; then
    git_fetch_exact_commit "$DEPLOY_COMMIT_SHA"
    git merge --ff-only "$DEPLOY_COMMIT_SHA"
fi
ACTUAL_HEAD="$(git rev-parse HEAD)"
if [ "$ACTUAL_HEAD" != "$DEPLOY_COMMIT_SHA" ]; then
    echo "ERROR: deployed HEAD mismatch: expected ${DEPLOY_COMMIT_SHA}, found ${ACTUAL_HEAD}"
    exit 1
fi

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
docker images pincheng-libreoffice:latest --format "{{.ID}}" > "${BACKUP_PATH}/libreoffice-image-hash.txt" 2>/dev/null || true

# ── 5. Validate Docker Compose configuration ──────────────────────────────
echo ">> Validating Compose configuration"
compose config --quiet || {
    echo "ERROR: Invalid Compose configuration"
    exit 1
}

# ── 6. Build new backend image ────────────────────────────────────────────
echo ">> Building backend image"
compose build backend 2>&1 | tail -5

# LibreOffice is a source-built service. Rebuild it on every application
# deployment so production cannot reuse an image with stale conversion limits
# or conversion code after a source commit changes.
echo ">> Building LibreOffice image"
docker compose -p "${COMPOSE_PROJECT}" \
    -f "${COMPOSE_BASE}" -f "${ORIGINAL_COMPOSE_OVERRIDE}" \
    --env-file "${COMPOSE_ENV_FILE}" \
    build libreoffice 2>&1 | tail -5

# ── 7. Apply and verify application schema migrations ─────────────────────
if [ "${SCHEMA_MIGRATION_ACTION}" = "APPLY_PENDING" ]; then
    echo ">> Stopping backend before application schema migration"
    compose stop backend
fi
echo ">> Checking application schema migration gate (${SCHEMA_MIGRATION_ACTION})"
compose run --rm --no-deps backend python -m scripts.migrate_app_schema \
    --db-path /app/data/app.sqlite \
    --backup-dir /app/data/migration-backups \
    --action "${SCHEMA_MIGRATION_ACTION}"

# ── 8. Deploy (rolling update) ────────────────────────────────────────────
echo ">> Deploying services"
compose up -d --no-deps --force-recreate backend 2>&1
compose up -d --no-deps --force-recreate libreoffice 2>&1

echo ">> Verifying LibreOffice runtime configuration"
LIBREOFFICE_CONTAINER="$(compose ps -q libreoffice)"
[ -n "${LIBREOFFICE_CONTAINER}" ] || {
    echo "ERROR: LibreOffice container identity is missing"
    exit 1
}
LIBREOFFICE_MAX_MB="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "${LIBREOFFICE_CONTAINER}" |
    awk -F= '$1 == "LIBREOFFICE_MAX_FILE_MB" {print $2; exit}')"
[ -n "${LIBREOFFICE_MAX_MB}" ] || {
    echo "ERROR: LibreOffice container is missing LIBREOFFICE_MAX_FILE_MB"
    exit 1
}
LIBREOFFICE_RUNTIME_MB="$(compose exec -T libreoffice python3 -c \
    'from app import MAX_FILE_SIZE; print(MAX_FILE_SIZE / 1024 / 1024)')"
python3 - "${LIBREOFFICE_MAX_MB}" "${LIBREOFFICE_RUNTIME_MB}" <<'PY'
import sys
expected = float(sys.argv[1])
actual = float(sys.argv[2])
if actual != expected:
    raise SystemExit(
        f"ERROR: LibreOffice runtime limit mismatch: env={expected:g}MB runtime={actual:g}MB"
    )
print(f"LibreOffice runtime limit verified: {actual:g} MB")
PY

# ── 8. Verify required backend media tools ────────────────────────────────
echo ">> Verifying backend media tools"
compose exec -T backend sh -lc '
    command -v ffmpeg >/dev/null 2>&1 || { echo "ERROR: ffmpeg not found in backend image"; exit 1; }
    command -v ffprobe >/dev/null 2>&1 || { echo "ERROR: ffprobe not found in backend image"; exit 1; }
    ffmpeg -version 2>&1 | sed -n "1p"
    ffprobe -version 2>&1 | sed -n "1p"
'

# ── 9. Wait for health ────────────────────────────────────────────────────
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

# ── 10. Qdrant health check ───────────────────────────────────────────────
echo ">> Checking Qdrant"
# Qdrant container doesn't have curl — use backend container to check.
compose exec backend curl -fsS http://qdrant:6333/collections/pincheng_docs \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  Qdrant OK: {d[\"result\"][\"points_count\"]} points')" \
    2>/dev/null || echo "  WARNING: Qdrant health check failed"

# ── 11. Final verification ────────────────────────────────────────────────
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
