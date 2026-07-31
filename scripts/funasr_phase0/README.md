# FunASR Phase 0 Sandbox (`scripts/funasr_phase0/`)

> Phase 0 non-production sandbox for the FunASR auto-transcription feature
> described in `project-docs/plans/funasr-auto-transcription.md`. This
> directory contains **test scaffolding only** — it MUST NOT be imported by
> `gpu_service/`, `src/`, `api/`, or `frontend/`.

## Status (2026-07-31, R3 first batch approved; maintenance window pending)

- 13 Python files + 3 PowerShell files + 1 requirements + 1 example
  config + 1 README = 19 files in this directory.
- 7 focused test files in `tests/`; tests use fake models, fake CUDA,
  fake `nvidia-smi`, temporary files and loopback HTTP only.
- `02/03/04` are guarded workers and refuse direct execution. The parent
  launcher owns license gating, active-run registration, monitoring,
  process-tree termination and BGE recovery verification.
- Independent third review is complete and the R2 sandbox repair is closed.
- **Not yet** executed on the production Windows GPU host. The user approved
  only R3-0 through R3-5, with `iic/SenseVoiceSmall@v1.0.0` as the sole ASR
  model. The approval did not include a concrete maintenance-window start/end,
  so dependency installation, model download and GPU execution remain blocked
  until those timestamps are supplied and written to the external config.

## Files (19 total)

| # | File | Type | Role |
|---|---|---|---|
| 1 | `__init__.py` | py | Package marker; declares public surface. |
| 2 | `phase0-config.example.json` | json | Loopback-only example config; production config lives outside repo. |
| 3 | `requirements-asr.txt` | txt | Sandbox-only deps (everything except torch/torchaudio). |
| 4 | `lib_config.py` | py | Config v2, aware approval window, numeric/path gates and pinned model identities. |
| 5 | `lib_metrics.py` | py | Pure-Python CER plus monotone DP segment alignment and RTF. |
| 6 | `lib_license_audit.py` | py | Scan installed packages and configured model artifacts; enforcing/report-only modes. |
| 7 | `lib_monitor.py` | py | Health/model identity/BGE latency/error/5xx/VRAM/disk fail-closed monitor. |
| 8 | `lib_runtime.py` | py | Nonce guard, active-run lifecycle, process-tree stop and BGE recovery. |
| 9 | `setup_venv.ps1` | ps1 | Create isolated venv; verify +cu128 and `pip check`. |
| 10 | `00_run_guarded.py` | py | Only supported launcher for GPU workers 02/03/04. |
| 11 | `01_measure_bge_baseline.py` | py | BGE-only baseline; immediate error/health abort. |
| 12 | `02_compat_smoke.py` | py | Guarded bounded CUDA compatibility worker. |
| 13 | `03_run_short.py` | py | Guarded eight-scenario ASR worker with hard metric verdicts. |
| 14 | `04_run_long.py` | py | Guarded reviewed-reference long ASR worker with real chunk durations. |
| 15 | `05_bge_coexist.py` | py | Same BGE instance, real 60-second rolling monitor and guarded ASR child. |
| 16 | `06_emergency_stop.ps1` | ps1 | Config-rooted active-run/PID/tree stop and BGE recovery. |
| 17 | `07_verify_bge.ps1` | ps1 | Config-rooted health/model-info/5 embed/1 rerank verification. |
| 18 | `08_annotate.py` | py | CPU-only provenance/reference validator. |
| 19 | `README.md` | md | This file. |

## Tests (7 files in `tests/`)

| # | File | Coverage |
|---|---|---|
| 1 | `test_funasr_phase0_metrics.py` | CER / RTF / code / BIM / segment / one-to-one (29 tests). |
| 2 | `test_funasr_phase0_monitor.py` | Fake BGE HTTP; lock-free stop; stop-once; run-isolation (7 tests). |
| 3 | `test_funasr_phase0_audio.py` | Cache key includes SHA; atomic rename; WAV validation (6 tests). |
| 4 | `test_funasr_phase0_baseline.py` | Health degraded; model-info mismatch; target_id matching (5 tests). |
| 5 | `test_funasr_phase0_config.py` | Config schema, timezone, threshold and CPU/GPU gate separation. |
| 6 | `test_funasr_phase0_entries.py` | Guard, fail-closed entry, license-before-spawn, active-run and annotation contracts. |
| 7 | `test_funasr_phase0_powershell.py` | PS5.1 parsing plus missing/corrupt/hash-drift emergency-stop behavior. |

## Approved first-batch execution order (R3-0 through R3-5 only)

1. R3-0: read-only host/GPU/BGE/disk/process preflight; record the current Git
   SHA and confirm the production worktree is clean before pulling.
2. R3-1: during the approved window, run `setup_venv.ps1` to create the isolated
   venv and freeze the resolved dependency set.
3. R3-2: configure only SenseVoiceSmall `v1.0.0`, the pinned VAD and punctuation
   models; download into the isolated model root; run `lib_license_audit.py` and
   stop on every blocker.
4. R3-3: run `01_measure_bge_baseline.py --config ...` for the 5-minute BGE-only
   baseline. Do not start ASR until it passes.
5. R3-4: run `00_run_guarded.py --config ... --step 02` for guarded CUDA/model
   compatibility smoke testing.
6. R3-5: validate the eight reviewed non-sensitive samples with `08_annotate.py`,
   then run `00_run_guarded.py --config ... --step 03 --manifest validated.jsonl`.
7. Stop and return the reports for review. Step 04, `05_bge_coexist.py`, 1h/2h/4h
   inputs and any alternative ASR model are outside this approval.

Keep `06_emergency_stop.ps1 -ConfigPath ... -ListOnly` ready and use `-WhatIf`
before the window. `07_verify_bge.ps1 -ConfigPath ...` is the recovery check.

R3-1 note (2026-07-31): the first production-host attempt stopped safely while
upgrading pip because Windows venvs reject self-upgrade through `pip.exe` and
PowerShell 5.1 promoted native stderr to `NativeCommandError`. The installer now
uses the venv's `python.exe -m pip` for upgrade/install/check/freeze, captures
stderr under `Continue`, and still fails closed on the native exit code. An
already-created incomplete venv is intentionally reused; no manual package
commands or directory deletion are required.

## Hard isolation rules (unchanged)

This sandbox MUST NOT:
- modify `gpu_service/`, `src/`, `api/`, `frontend/`, `prompts/`,
  `docker/`, `requirements.txt`, `requirements-prod.txt`,
  `requirements-gpu.txt` of the project;
- touch the production `app.sqlite`, `parents.sqlite`, `media/`,
  `docs/`, `data/parsed/`, or any Qdrant collection;
- start a second BGE instance on port 18100 (R2 fix: removed entirely);
- download `BAAI/bge-m3` weights (the coexistence test uses the same
  production BGE instance);
- silently fall back to CPU when CUDA is unavailable;
- modify the production Windows host without an explicit R3 plan;
- auto-deploy on push (CI/CD is intentionally NOT changed — Path A).

## Key R2 fixes (per user audit)

1. RTF = `inference_wallclock_s / audio_duration_s` (consistent
   across the sandbox); `realtime_speedup` is the inverse.
2. `python-Levenshtein` removed (GPL risk); pure-Python edit distance
   only.
3. Codes: GB/JGJ/CJJ with `JGJ/T` / `JGJ-T` normalized to `jgj/t`;
   year optional in canonical match; precision / recall / FP /
   per-item detail.
4. BIM terms: precision / recall / per-term FP / FN / TN / verdict.
5. Segments: monotone dynamic-programming one-to-one matching; start/end
   drift p50/p95/p99/max; omission / extra / consecutive repeat rates.
6. `lib_monitor` callbacks are invoked **outside any lock** and at
   most once; threads fail-closed on exception; health parsed as JSON.
7. Audio extract cache key includes source SHA-256, sample rate,
   channel count, decoder version. Old / same-name / half-done inputs
   are not reused.
8. Each ASR/VAD/punctuation identity and revision is passed explicitly;
   each model loads **once per guarded worker**.
9. BGE coexistence uses the **same production BGE instance**; no
   local copy. Embed / Rerank P95 are compared separately; rolling
   60s windows. No waiting 1h to judge degradation.
10. The parent enforces the license audit before it creates a GPU worker.
11. GPU entry scripts take `--config`, require a current approval window and
    reject direct execution without a parent nonce guard.
    `shared_production_gpu_confirmed: true` and a current
    `approved_window`.
12. PowerShell scripts use `$processId` (not the automatic `$PID`),
    no `??` / `?:` (PS5.1 compatible), support `-WhatIf` /
    `-ListOnly`, and never include the BGE token in any report.
13. Emergency stop never reports success without an active-run outcome;
    config-hash drift still stops the exact PID after start-time and command
    verification, records an integrity warning, and exits non-zero.
14. Annotation drafts are limited to 8 MiB / 5,000 physical lines /
    64 KiB per line; optional `license_evidence` is preserved as an advisory.
15. Short-sample warm-up uses the first reviewed manifest sample so VAD,
    ASR and punctuation paths are exercised before measured inference.

## What is NOT in this directory (unchanged)

- No Phase 1 code (audio extraction adapter, Canonical JSON,
  formatter).
- No `transcription_jobs` schema migration or `app.sqlite` change.
- No `asr_service` HTTP server.
- No BGE weights download script.
- No GPU workload code that mutates `gpu_service/`.
- No local BGE copy or :18100 port.

## Local dev venv (separate from sandbox)

The author (Claude) maintains a separate venv at
`E:\Workspace\funasr-phase0-dev\.venv` (Python 3.11.9) for static
verification only. See `E:\Workspace\funasr-phase0-dev\README.md` for
details. The production sandbox uses `C:\FunASR-Phase0\venv`
(Python 3.10) created by `setup_venv.ps1`.

## Sync strategy

- The R2 fix does **not** modify CI/CD (`.github/workflows/*`).
- Future sync to the production Windows GPU host: via a **separate
  Git checkout** fixed to the approved commit SHA, not by modifying
  the current `gpu_service` worktree.
- Manual approval remains required for any production deploy.

## Validation

- 74/74 focused CPU-only unit, entry-point and PowerShell behavior tests
  passed together after the third-review closeout.
- 20/20 sandbox Python source/test files compiled in memory without writing
  bytecode.
- 3/3 PowerShell files passed the Windows PowerShell 5.1 parser.
- No validation in this section authorizes GPU or production execution.
