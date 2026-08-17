# 品成 BIM 知识库 (PinCheng BIM Knowledge Base)

An internal Chinese-language RAG system for a BIM consultancy. It indexes the company's accumulated knowledge — **industry codes, customer requirements, internal standards, past-project deliverables, and training-video transcripts** — and answers natural-language questions with citations like `[doc §section]` or `[doc @HH:MM:SS]`.

---

## Quick start — local

**Requirements:** Python 3.11+, Node.js 18+, ~10 GB disk, a running Qdrant instance.

```bash
# Start Qdrant (or use the Docker path below)
docker run -d -p 6333:6333 qdrant/qdrant

# Backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in ZHIPU_API_KEY, MINERU_API_KEY, ADMIN_EMPLOYEE_ID, ADMIN_PASSWORD
uvicorn api.main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend && npm install && npm run dev   # http://localhost:5173
```

First boot seeds an admin account from `ADMIN_EMPLOYEE_ID` / `ADMIN_PASSWORD`. Self-registration is open — staff sign up at `/register`.

To build the legacy index from files in `content/legacy-docs/`:

```bash
python scripts/build_index.py
```

---

## Quick start — Docker (self-hosted server)

Two services: `qdrant` (vector store) and `backend` (FastAPI serving both `/api/*` and the React SPA on port 80).

```bash
cp .env.example .env   # fill in ZHIPU_API_KEY, MINERU_API_KEY, ADMIN_EMPLOYEE_ID, ADMIN_PASSWORD
docker compose -f docker/docker-compose.yml build
docker compose -f docker/docker-compose.yml up -d
docker compose -f docker/docker-compose.yml logs -f backend   # watch first-boot model download (~3 GB)
```

First boot takes ~30s (no GPU model downloads). The backend image bundles the built React app (multi-stage Dockerfile runs `npm run build` in a node stage), so there's no separate frontend container or nginx proxy.

**Architecture:** GPU inference (BGE-M3 embedding + BGE-reranker) runs on a separate Windows GPU host, accessed via HTTP. The Ubuntu host runs only the API backend + Qdrant vector database. See `docs/migrations/ubuntu-app-windows-gpu-runbook.md` for details.

**Accessing the app** once `docker compose ps` shows `backend` as `healthy`:

- **Same machine** (Mac/Linux dev): open `http://localhost/` in a browser.
- **Remote server**: open `http://<server-ip>/` (port 80 is published by the compose file). On a LAN you can also use the hostname (`http://<hostname>.local/` on macOS/Bonjour).
- **First login**: use the `ADMIN_EMPLOYEE_ID` / `ADMIN_PASSWORD` you set in `.env` — that account is auto-seeded on first boot. From the admin dashboard at `/admin` you can register additional users.
- **Health check**: `curl http://localhost/api/health` should return `{"status":"ok"}` before the SPA is usable.
- **HTTPS**: the container only speaks HTTP on :8000 (mapped to host :80). For production HTTPS, put a reverse proxy (Caddy, nginx, Cloudflare Tunnel, your cloud LB) in front and set `SESSION_COOKIE_SECURE=true` in `.env` (the default).

> Env wiring: the repo-root `.env` is the single source of truth. `docker/.env` is a symlink to it so Compose v2's project-directory `.env` discovery resolves `${VAR}` substitutions (e.g. `BUILD_PLATFORM`); the backend service also lists `../.env` under `env_file:` so all keys land inside the container at runtime. If you clone fresh, recreate the symlink with `ln -s ../.env docker/.env`.

**Build initial index** (only needed when adding documents via the filesystem directly):

```bash
docker compose -f docker/docker-compose.yml exec backend python scripts/build_index.py
```

**Update code:**

```bash
git pull && docker compose -f docker/docker-compose.yml build && docker compose -f docker/docker-compose.yml up -d
```

**Useful env vars** (in `.env`):
- `ZHIPU_API_KEY` — required for generation
- `MINERU_API_KEY` — recommended; enables fast cloud PDF parsing (~1 min/PDF vs 30+ min local)
- `ADMIN_EMPLOYEE_ID` / `ADMIN_PASSWORD` — bootstrap admin on first boot
- `SESSION_COOKIE_SECURE` — set `false` for plain HTTP dev
- `HF_ENDPOINT=https://hf-mirror.com` — HuggingFace mirror for restricted networks
- `LLM_MODEL` / `LLM_REWRITE_MODEL` — override default models (`glm-4.7-flashx` / `glm-4.5-air`)

---

## Adding documents

**Via admin UI** (`/admin` → 资料管理 → 上传资料): upload `.pdf` or `.md` files directly from the browser. PDFs are parsed by MinerU, chunked, and embedded automatically; progress is shown under 资料管理 → 索引任务. No shell access needed.

**Via filesystem + CLI** (for bulk loads):

```bash
cp new_standard.pdf content/legacy-docs/行业规范/
python scripts/build_index.py   # incremental — only new files are processed
```

Legacy document categories are derived from the first-level folder under `content/legacy-docs/` (or the explicit `DOCS_DIR`). Only `客户标准` uses a second level (`客户标准/<customer>/`). `.md` files in `教学视频/` are treated as video transcripts (speaker-turn + timestamp chunking); `.md` elsewhere is chunked like a parsed PDF. The repository `docs/` directory contains project documentation and is never a business-content source.

---

## Debugging

```bash
# Retrieval only — no LLM, no API key
python scripts/test_retrieve.py "Q345 钢手工焊用什么焊条？"

# Full RAG with debug output (requires ZHIPU_API_KEY)
python scripts/eval_query.py "Q345 钢手工焊用什么焊条？"
# drops into REPL; /reset /history /full /short /exit
```

---

## Evaluation

A retrieval-graded golden set lives in `src/eval/`. As of 2026-07-30 the
rebuilt set has 79 items: 32 factual, 4 table_formula, 23 code_lookup,
10 multi_turn (5 pairs), 6 no_answer, 4 comparison.

```bash
python scripts/run_eval_retrieval.py                       # prints by-kind R@1, R@5, MRR@5
python scripts/run_eval_retrieval.py --kinds comparison   # only comparison items
python scripts/run_eval_retrieval.py --strict-staleness  # fail on fingerprint drift
python scripts/diff_eval_runs.py <a>.jsonl <b>.jsonl    # compare two runs (legacy)
```

The current run also writes `src/eval/runs/run_<ISO>.summary.json`
sidecar with the run protocol metadata (config snapshot, fingerprint
status, ITT / applied-only aggregates, fixed `decision_eligible=false`
Phase A disclaimer). The JSONL stays per-item only; `diff_eval_runs.py`
indexes by `item_id` and keeps working unchanged.

**Phase A disclaimer** (always printed for the comparison section): the
comparison path runs `retrieve()` vs `retrieve_multi()` **in-process**,
not through the production `QUERY_DECOMPOSE_ENABLED` switch, and does
not exercise rewrite / guard / carry / context packaging / answer
quality. Phase A output is **descriptive, not decision-grade** — it
exists to measure the retrieval mechanism, not to justify enabling the
flag. Real A/B on a canary environment is Phase B (separate plan).

Current baseline (production index, 2026-07-30): **R@1 ≈ 75.4%, R@5 = 100%, MRR@5 ≈ 0.870, no-answer 6/6**. (The
older "R@1 = 90%, R@5 = 96%" line reflects a now-superseded
steel-structure corpus; do not compare the two numbers directly.)

---

## How it works

```
PDF / .md                  parsed markdown    chunks         vectors          answer
content/legacy-docs/<category>/ →(1)→ data/parsed/ →(2)→ parent+ →(3)→ Qdrant →(4)→ GLM-4
                   MinerU                       child        SQLite            citations
                                                             BGE-M3 + reranker
```

1. **Parse** — PDFs → markdown via MinerU. `.md` files skip this step.
2. **Chunk** — `chunk.py` splits by markdown headers into parent (1200 char) / child (256 char) pairs. Tables and formulas are kept atomic. Transcripts split by speaker turn; each chunk carries a `HH:MM:SS` timestamp.
3. **Embed + Index** — BGE-M3 produces dense + sparse vectors in one pass → Qdrant (server mode). Parent text → `data/parents.sqlite`.
4. **Retrieve + Rerank + Generate** — hybrid dense+sparse RRF retrieval with optional code-boost (detects standard codes like `GB 50017`), BGE-reranker-v2-m3 cross-encoder rerank, then Zhipu GLM-4 with strict citation rules.

**Multi-turn** (`src/session.py`): query rewriter resolves follow-ups; top 2 sources from the previous turn carry forward; context budget shrinks dynamically as history grows.

**HTTP layer** (`api/`): FastAPI with SSE streaming, server-side session cookie auth (`pc_sid`), CSRF token on mutating requests. In production the same FastAPI process also serves the React bundle at `/` (mounted via `SPAStaticFiles` with index.html fallback for client-side routes). Admin endpoints cover user management, conversation browsing, feedback log, and the document upload/indexing queue.

See `CLAUDE.md` for architecture invariants and what to be careful about when editing.
