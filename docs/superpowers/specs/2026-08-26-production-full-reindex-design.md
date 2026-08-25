# Production Full Reindex Design

## Goal

Rebuild every currently published managed document and transcript into a fresh
Parent database and Qdrant collection so citation location metadata is
regenerated without exposing a partially rebuilt index to production traffic.

## Architecture

The rebuild process reads `app.sqlite` in read-only mode and treats
`content_item_heads` and `media_transcript_heads` as the complete visibility
snapshot. It writes only to an isolated data directory and a run-scoped Qdrant
collection. Managed PDFs and Office files are reparsed into the isolated cache,
so old Markdown caches cannot suppress the new location sidecars.

The production workflow obtains both existing production locks, rejects active
application jobs, verifies the exact `master` commit, and creates independent
SQLite and Qdrant backups before building. It validates head coverage,
Parent/Child identity, location coverage, SQLite integrity, and Qdrant health
before stopping the backend for the cutover. The shadow Qdrant snapshot is
restored into `pincheng_docs`, and the shadow Parent database replaces
`parents.sqlite`. Any post-cutover failure restores both pre-run artifacts.

## Components

- `src/config.py`: opt-in environment overrides for rebuild-only data and
  collection destinations; existing defaults remain unchanged.
- `src/indexing_pipeline.py`: explicit rebuild options to force parse and avoid
  writing preview artifacts into managed content.
- `scripts/rebuild_managed_index.py`: deterministic head enumeration, verified
  source/artifact loading, indexing, and a machine-readable verification report.
- `.github/workflows/rebuild-production-index-manual.yml`: guarded production
  backup, shadow build, validation, cutover, rollback, and evidence retention.

## Failure Handling

Failures before cutover delete only the run-scoped shadow collection and leave
production untouched. Failures after cutover restore the downloaded pre-run
Qdrant snapshot and Parent database, then require the backend to become healthy.
The application database and content objects are never replaced or deleted.

## Validation

Static tests protect workflow confirmation, exact-SHA checks, locks, backups,
active-job gates, shadow destinations, rollback, and forbidden volume deletion.
Python unit tests cover configuration overrides, head enumeration, object hash
verification, transcript reconstruction, and report validation. CI and the
production workflow provide the final Linux/container evidence.

