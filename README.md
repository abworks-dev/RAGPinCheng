# PinCheng BIM Knowledge Base

[中文版](README_zh.md) | English

An internal Chinese-language knowledge system for a BIM consultancy. It turns
industry codes, customer requirements, company standards, project material,
and training-video transcripts into answers with verifiable citations.

This repository contains the RAG pipeline, FastAPI application, React client,
managed content workflows, and separately deployable GPU, ASR, and Office
conversion services.

## Current capabilities

- Hybrid dense and sparse retrieval with reranking, query rewriting, multi-turn
  context, streaming answers, and section or timestamp citations.
- Session-cookie authentication, CSRF protection, user administration,
  conversation history, feedback review, and maintenance tools.
- A managed content library with classification, review, publication, versioned
  artifacts, indexing jobs, source preview, and granular permissions.
- PDF and Markdown ingestion. DOCX, XLSX, and PPTX are supported when Office
  processing and the LibreOffice conversion service are enabled.
- Versioned video transcripts, manual transcript editing, review, publication,
  and optional remote ASR profiles.

Feature availability is controlled by configuration. In particular,
`CONTENT_MANAGEMENT_ENABLED`, `ASR_ENABLED`, and `QUERY_DECOMPOSE_ENABLED` are
disabled by default. See [.env.example](.env.example) before enabling them.

## Architecture

```text
managed publication or content/legacy-docs (compatibility input)
  -> MinerU / Markdown / Office conversion
  -> parent-child chunks
  -> BGE-M3 dense+sparse vectors in Qdrant + parents in SQLite
  -> RRF and code boost -> BGE reranker
  -> ChatSession -> GLM answer and citations
  -> FastAPI SSE -> React
```

The application keeps two SQLite responsibilities separate:
`data/parents.sqlite` is rebuildable RAG state, while `data/app.sqlite` stores
users, permissions, conversations, and managed-content state and must survive
index rebuilds.

## Local development

Requirements: Python 3.11+, Node.js 18+, Docker, and about 10 GB of free disk.

```bash
# Vector database
docker run -d --name pincheng-qdrant -p 6333:6333 qdrant/qdrant:v1.18.3

# Python environment and backend
python -m venv .venv
# Activate .venv with the command for your shell, then:
python -m pip install -r requirements.txt
cp .env.example .env
uvicorn api.main:app --reload --port 8000

# Frontend, in another terminal
cd frontend
npm install
npm run dev
```

Set at least `ZHIPU_API_KEY`, `MINERU_API_KEY`, `ADMIN_EMPLOYEE_ID`, and
`ADMIN_PASSWORD` in `.env`. For plain-HTTP local development, also add
`SESSION_COOKIE_SECURE=false`. The frontend runs at `http://localhost:5173`;
the backend API runs at `http://localhost:8000/api`.

The first backend start seeds an administrator when the configured employee ID
does not already exist. Staff can also register through `/register`.

## Docker deployment

The production Compose stack runs Qdrant, the FastAPI/React backend, and the
LibreOffice conversion service. Embedding and reranking are expected to use the
separate Windows GPU service.

```bash
cp .env.example .env
docker compose -f docker/docker-compose.yml build
docker compose -f docker/docker-compose.yml up -d
docker compose -f docker/docker-compose.yml ps
curl http://localhost/api/health
```

Do not treat this quick start as a production runbook. TLS, secrets, storage,
remote GPU configuration, backup, and managed-content cutover are covered by
the documents linked below.

## Adding content

The managed library is the primary content workflow. After it is explicitly
enabled for the target environment, administrators use the content management
pages to classify, review, publish, index, preview, and retire documents.
Only published versions become formal retrieval sources.

`content/legacy-docs/` remains a compatibility input for filesystem-based bulk
loads:

```bash
python scripts/build_index.py
```

Do not place project documentation or real customer material in the repository.
The tracked `docs/` tree is project documentation and is never an ingestion
source.

## Validation and debugging

```bash
# Retrieval only; no answer-generation request
python scripts/test_retrieve.py "Q345 steel manual welding electrode"

# Full RAG diagnostic; requires the configured LLM
python scripts/eval_query.py "Q345 steel manual welding electrode"

# Retrieval golden set and fingerprint check
python scripts/run_eval_retrieval.py --strict-staleness
```

Evaluation results depend on the indexed corpus, configuration, and index
fingerprint. Historical metrics in plans or old run artifacts are not a claim
about the current checkout or production state.

## Documentation

- [Project documentation map](docs/README.md)
- [Current feature map](docs/features/README.md)
- [Page, permission, API, and test inventory](docs/design/page-inventory.md)
- [IT deployment guide](docs/operations/部署指南_IT.md)
- [Ubuntu application and Windows GPU runbook](docs/migrations/ubuntu-app-windows-gpu-runbook.md)
- [Managed content production runbook](docs/migrations/managed-content-production-runbook.md)
- [Office conversion operations](docs/operations/OFFICE_CONVERSION.md)
- [User acceptance guide](docs/USER_ACCEPTANCE.md)
- [Development rules and architecture invariants](CLAUDE.md)
- [Active product work](TODO.md)

## Security

Never commit `.env`, credentials, customer documents, user conversations,
SQLite databases, Qdrant storage, model caches, or generated transcription
artifacts. Index resets, destructive migrations, production deployment, and
real-data processing require an environment-specific backup and approval plan.
