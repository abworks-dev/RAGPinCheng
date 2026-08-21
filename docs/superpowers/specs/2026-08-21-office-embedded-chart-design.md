# Office Embedded Chart Safety Design

## Goal

Accept PowerPoint files whose charts contain the normal embedded Excel workbook while preserving closed-by-default rejection for unsafe Office relationships and payloads.

## Current Cause

`src/office_security.py` rejects every ZIP member containing `embeddings/`. PowerPoint chart data is commonly stored as an embedded `.xlsx`, so the current rule rejects a benign chart as `office_embedded_object`.

## Safety Policy

- Reject any external relationship in the outer package or an allowed embedded workbook.
- Reject OLE binary objects, macro payloads, unsupported embedded formats, unreferenced embedded files, malformed packages, and nested embedded packages.
- Allow only an embedded `.xlsx` whose relationship is a package relationship from a PowerPoint chart part; recursively validate that workbook before allowing it.
- Keep existing stable rejection codes so API and UI behavior remain compatible.

## Scope

The change is limited to the shared OOXML safety checker and synthetic regression coverage. It does not rewrite Office files, change Docling/openpyxl/LibreOffice behavior, alter index schemas, rebuild indexes, or delete existing data.

## Verification

Run the Office security and managed/legacy upload tests, then run the targeted Python syntax/import checks. Confirm the safe chart fixture is accepted and unsafe OLE, external-link, malformed, and unreferenced-embedding fixtures remain rejected.
