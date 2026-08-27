# PPTX Location Degradation Design

## Goal

Improve reliable PPTX slide attribution without allowing missing slide metadata to block an otherwise valid production index rebuild.

## Current Behavior

PPTX source extraction creates one concatenated text anchor per non-empty slide. Chunk location matching requires a contiguous probe between that anchor and Docling Markdown. Text boxes, tables, and drawing order can therefore produce valid chunks with no slide match. The production rebuild currently treats any locatable managed head with no located parent as a fatal integrity failure.

Production diagnostics established both required cases:

- `中海地下室吊架方案_V1.0.pptx` produces 70 parents, with 12 located parents. It must remain indexable and should gain coverage where reliable short anchors match.
- `RAGPinCheng PPTX 解析测试报告.pptx` produces one parent and three children, all unlocated. It must remain indexable even if no reliable slide can be assigned.

## Design

### PPTX anchors

Read text runs from each source slide in presentation order. Emit multiple normalized anchor candidates per slide instead of only one concatenated string. Prefer complete text blocks and useful individual lines; discard empty anchors and deduplicate identical text within the same slide. Preserve the source slide number on every anchor.

The existing conservative matcher remains authoritative. A location is written only when source text and chunk text share the existing deterministic probe rule. No fuzzy similarity threshold, nearest-slide fallback, or ordinal guess may write a slide number.

### Degraded location state

Missing `slide_number` remains represented by `None` on Parent and Child records. No database or payload schema changes are required. Such chunks remain searchable and cite the document, but clients must not receive an invented slide jump.

### Production rebuild validation

Index correctness remains a hard gate: all frozen heads must be indexed, every source hash must match, parents and children must be non-empty, `parents.sqlite` integrity must pass, Qdrant must be green, and exact point count must equal indexed children.

Location coverage becomes an observable quality metric rather than a hard gate. The report records expected locatable heads, located heads, zero-coverage heads, partially located heads, located and total parents by document type, without document text or filenames. Zero or partial PPTX coverage does not fail validation.

### Production delivery

The change is delivered through one PR. After merge and deployment, the full rebuild runs against a shadow Qdrant collection and shadow `parents.sqlite`. Only an otherwise valid report permits the existing atomic promotion. Failure before promotion leaves the current production index active.

## Scope

Modify PPTX anchor extraction, rebuild coverage reporting and validation, focused tests, and current feature documentation if its stated contract changes. Do not modify application SQLite schemas, content lifecycle states, publication actions, chunk IDs, embedding inputs, source files, or preview conversion.

## Verification

- Unit tests for reordered PPTX text, short text blocks, duplicate anchors, and no reliable match.
- Chunk integration tests proving reliable anchors propagate slide numbers and unlocated chunks remain `None`.
- Rebuild tests proving index integrity still fails hard while zero location coverage reports a warning-quality metric and passes validation.
- Existing Office conversion, location, production rebuild, and workflow tests.
- Production shadow rebuild report and promotion checks after merge.

## Rollback

Revert the PR to restore prior anchor and validation behavior. The shadow rebuild is non-destructive until promotion; if verification fails, do not promote it. If a post-promotion regression is found, restore the previous production collection and `parents.sqlite` using the existing deployment rollback unit.
