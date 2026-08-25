# Production Full Reindex Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add and run a guarded production full-index rebuild that regenerates citation locations from all current heads.

**Architecture:** Build against an isolated Parent SQLite file, parse cache, and Qdrant collection. Verify the shadow result before a short backend cutover, with automatic restoration from independent backups on failure.

**Tech Stack:** Python, SQLite, Qdrant HTTP/client, Docker Compose, GitHub Actions Bash, pytest.

---

### Task 1: Isolated index destinations

**Files:**
- Modify: `src/config.py`
- Modify: `src/indexing_pipeline.py`
- Test: `tests/test_production_full_reindex.py`

- [ ] Write failing tests for opt-in data/collection overrides and force-parse options.
- [ ] Run `python -m pytest -q tests/test_production_full_reindex.py` and confirm the missing contracts fail.
- [ ] Implement environment overrides with unchanged defaults and rebuild-only parse options.
- [ ] Re-run the focused tests.

### Task 2: Current-head rebuild script

**Files:**
- Create: `scripts/rebuild_managed_index.py`
- Test: `tests/test_production_full_reindex.py`

- [ ] Write failing tests for managed/transcript head enumeration and verified object loading.
- [ ] Implement read-only snapshot enumeration and deterministic indexing orchestration.
- [ ] Add report validation for exact head coverage, nonzero Parent/Child counts, location statistics, SQLite integrity, and Qdrant green status.
- [ ] Run focused tests and Python compile checks.

### Task 3: Guarded production workflow

**Files:**
- Create: `.github/workflows/rebuild-production-index-manual.yml`
- Test: `tests/test_production_full_reindex_workflow.py`

- [ ] Write failing static tests for confirmation, exact commit, locks, active-job gate, backups, isolated build, cutover, rollback, and forbidden commands.
- [ ] Implement the manual workflow using the production app runner and existing Compose contract.
- [ ] Run workflow static tests and parse the workflow with the available YAML tooling.

### Task 4: Documentation and delivery

**Files:**
- Modify: `docs/features/document-indexing.md`
- Modify: `docs/operations/部署指南_IT.md`

- [ ] Document the guarded entry point, invariants, evidence, and rollback.
- [ ] Run relevant Python tests, compile checks, workflow tests, and CI-equivalent static checks.
- [ ] Run `scripts/Test-CodexDelivery.ps1`, create one PR, wait for required checks, and merge.
- [ ] Trigger the workflow with the merged full `master` SHA and follow it through verification or rollback.
