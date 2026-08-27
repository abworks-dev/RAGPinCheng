# PPTX Location Degradation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve conservative PPTX slide attribution and allow valid indexes with unlocated PPTX chunks to pass production rebuild verification.

**Architecture:** Source PPTX text blocks become multiple slide-numbered location anchors consumed by the existing conservative matcher. Production rebuild reports head and parent coverage as quality telemetry while retaining all structural, hash, database, and Qdrant correctness gates.

**Tech Stack:** Python 3.11, OOXML/ElementTree, pytest, SQLite, Qdrant, GitHub Actions

**Spec:** `docs/superpowers/specs/2026-08-27-pptx-location-degradation-design.md`

## Global Constraints

- Never infer a slide number from ordinal position, fuzzy similarity, or nearest-slide fallback.
- An unlocated Parent or Child uses existing `None` fields and remains indexable.
- Do not change Chunk IDs, embedding text, SQLite schemas, publication states, source files, or preview behavior.
- Production rebuild promotion remains shadow-first and requires every non-location integrity gate to pass.

---

### Task 1: Fine-grained PPTX source anchors

**Files:**
- Modify: `src/office_convert.py`
- Test: `tests/test_office_conversion_resilience.py`

**Interfaces:**
- Consumes: `_pptx_source_slides(path: Path) -> list[dict[str, Any]]`
- Produces: multiple `{ "slide_number": int, "text": str }` anchors per slide, in slide and source-text order

- [ ] **Step 1: Add failing extraction and chunk-location tests**

Add synthetic PPTX cases where the source slide contains multiple text blocks while Docling Markdown reorders or isolates one block. Assert every useful source block is emitted with the correct slide number, duplicates are removed per slide, and a matching block reaches Parent and Child `slide_number`.

- [ ] **Step 2: Run the focused tests and confirm the concatenated-anchor implementation fails**

Run: `python -m pytest tests/test_office_conversion_resilience.py -k "pptx and (source or slide)" -q`

Expected: at least the new reordered-block assertion fails because only one concatenated anchor is returned.

- [ ] **Step 3: Implement minimal block-level extraction**

Update `_pptx_source_slides` to collect non-empty DrawingML text at a useful block boundary, normalize whitespace, deduplicate exact text per slide, and emit each retained anchor with its source slide number. Keep safe OOXML path checks and the current parse-failure fallback.

- [ ] **Step 4: Verify reliable and degraded matching**

Run: `python -m pytest tests/test_office_conversion_resilience.py tests/test_document_locations.py -q`

Expected: all tests pass; reliable anchors carry slide numbers and unmatched content remains unlocated.

- [ ] **Step 5: Commit the anchor change**

Commit: `fix: improve conservative PPTX slide anchors`

### Task 2: Location coverage as production quality telemetry

**Files:**
- Modify: `scripts/rebuild_managed_index.py`
- Test: `tests/test_production_full_reindex.py`
- Test: `tests/test_production_full_reindex_workflow.py`

**Interfaces:**
- Consumes: rebuilt Parent rows grouped by `content_version_id` and `doc_type`
- Produces: `location_head_coverage` and aggregate parent coverage without document identifiers or content

- [ ] **Step 1: Add failing validator and report tests**

Add a report with complete managed/transcript/Qdrant integrity and `location_head_coverage.located < expected`; assert `validate_report` accepts it. Assert malformed index counts, failed integrity, non-green Qdrant, and point-count mismatch still raise. Add aggregate reporting assertions for zero and partial coverage without names or text.

- [ ] **Step 2: Run focused rebuild tests and confirm the old hard gate fails**

Run: `python -m pytest tests/test_production_full_reindex.py tests/test_production_full_reindex_workflow.py -q`

Expected: the new degraded-coverage test fails with `location_head_coverage_mismatch`.

- [ ] **Step 3: Remove only the location equality hard gate and enrich aggregates**

Keep `validate_report` checks for head coverage, non-empty index, SQLite integrity, Qdrant green state, and exact child count. Calculate zero-coverage and partial-coverage head counts plus located/total parent counts by document type. Ensure console output stays aggregate-only.

- [ ] **Step 4: Run production rebuild tests**

Run: `python -m pytest tests/test_production_full_reindex.py tests/test_production_full_reindex_workflow.py -q`

Expected: all tests pass and no document metadata appears in location verification output.

- [ ] **Step 5: Commit the rebuild contract change**

Commit: `fix: degrade missing document locations during rebuild`

### Task 3: Regression verification and delivery

**Files:**
- Modify only if required by current facts: `docs/features/content-library.md`

**Interfaces:**
- Consumes: Tasks 1 and 2
- Produces: one reviewed PR and an approved production shadow rebuild

- [ ] **Step 1: Run the focused regression suite**

Run: `python -m pytest tests/test_office_conversion_resilience.py tests/test_document_locations.py tests/test_production_full_reindex.py tests/test_production_full_reindex_workflow.py tests/test_single_pptx_location_diagnostic.py -q`

Expected: all tests pass.

- [ ] **Step 2: Run static verification**

Run: `python -m py_compile src/office_convert.py scripts/rebuild_managed_index.py`

Run: `git diff --check`

Expected: both commands exit zero.

- [ ] **Step 3: Run delivery gate and create one PR**

Run: `pwsh -NoProfile -File scripts/Test-CodexDelivery.ps1 -Repository abworks-dev/RAGPinCheng`

Create a PR containing risk R3, approved scope, actual tests, production shadow behavior, and rollback instructions. Wait for all required checks and merge only when green.

- [ ] **Step 4: Deploy and run one production shadow rebuild**

Use the repository's approved production deployment and manual full-reindex workflows from `master`. Do not change workflow inputs, hosts, credentials, firewall, or traffic scope. Retry only same-scope transient failures.

- [ ] **Step 5: Verify promotion evidence**

Confirm the report has complete head coverage, non-empty parents/children, `parents_integrity=ok`, green Qdrant, exact child count, aggregate location telemetry, and successful atomic promotion. If any non-location gate fails, leave the existing production index active and report the blocker.
