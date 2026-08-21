## Risk
- [ ] R0
- [ ] R1
- [x] R2
- [ ] R3

## Scope
- Add the approved production external source root to the manual app deployment environment.
- Include it as a read-only `/app/external-sources` bind in the source-decoupled Compose sanitizer.
- Add regression coverage. No database, SMB credentials, or task data changes.

## Validation
- `python -m pytest -q tests/test_source_decoupled_override.py` (9 passed)
- `git diff --check` passed.

## Rollback
Revert commit `798af713`; deployment backup and automatic rollback remain enabled.

## Approval Evidence
User explicitly requested `修复部署` after the previous deployment rolled back.
