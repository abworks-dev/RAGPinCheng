# Office Embedded Chart Safety Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Permit safe chart-linked embedded Excel workbooks in PPTX uploads without permitting external links, OLE objects, macros, or arbitrary embedded files.

**Architecture:** Keep the existing `find_unsafe_office_content(path)` API and replace its directory-name heuristic with structured OOXML relationship parsing. Derive relationship source parts and normalized targets, allow only chart-to-package `.xlsx` targets, and recursively scan the allowed workbook from in-memory ZIP bytes. All other embedded content remains rejected.

**Tech Stack:** Python 3, `zipfile`, `xml.etree.ElementTree`, pytest, existing FastAPI upload fixtures.

---

### Task 1: Add failing security regressions

**Files:**
- Modify: `tests/test_office_upload_security.py`

- [ ] Add a synthetic PPTX fixture helper that writes a chart relationship from `ppt/charts/chart1.xml.rels` to `ppt/embeddings/Microsoft_Excel_Worksheet1.xlsx`, with a minimal nested XLSX package.
- [ ] Add a test asserting this chart-linked workbook currently returns `office_embedded_object`; this must fail after the expected behavior is asserted as `None`.
- [ ] Add tests asserting unreferenced embedded `.xlsx`, OLE `.bin`, external relationships, nested external relationships, and malformed embedded packages remain rejected with stable codes.
- [ ] Run `E:\Repository\Github\RAGPinCheng\.venv\Scripts\python.exe -m pytest tests/test_office_upload_security.py -q` and verify the new allowlist test fails for the current heuristic.

### Task 2: Implement structured allowlist scanning

**Files:**
- Modify: `src/office_security.py`

- [ ] Parse `.rels` XML with `xml.etree.ElementTree`; treat any `TargetMode="External"` or external hyperlink target as `office_external_link`.
- [ ] Resolve each relationship target against its source part using POSIX normalization.
- [ ] Allow an embedded member only when it is an `.xlsx` target of a package relationship whose source part is under a PowerPoint `charts/` directory; scan that nested ZIP recursively.
- [ ] Return `office_embedded_object` for OLE/binary, unsupported, unreferenced, or nested embedded content; return `office_package_invalid` for malformed ZIP/XML or missing referenced members.
- [ ] Keep the public function signature and existing stable codes unchanged.

### Task 3: Verify upload integration

**Files:**
- Modify: `tests/test_content_library_api.py`
- Modify: `tests/test_routes_admin_documents.py` only if a legacy integration regression is needed after the helper tests.

- [ ] Add a managed-upload test using the synthetic chart package and assert `status="accepted"`.
- [ ] Assert an OLE package is still skipped and no content version is created.
- [ ] Run targeted Office, managed upload, and legacy upload tests; run `compileall` on changed Python modules.

### Task 4: Review and delivery evidence

- [ ] Inspect the complete diff for scope creep and ensure no real documents, databases, or secrets are present.
- [ ] Run the required verification commands from the linked worktree and record exact pass/fail counts.
- [ ] Request an independent code review before merge and resolve all important findings.
- [ ] Run `scripts/Test-CodexDelivery.ps1 -Repository abworks-dev/RAGPinCheng` before any PR/merge action.

