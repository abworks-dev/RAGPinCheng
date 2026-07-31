"""FunASR Phase 0 sandbox — package marker.

All sandbox entry scripts and libraries live here. Imports use
``from scripts.funasr_phase0.<module> import ...`` from the repo root
or after ``sys.path.insert(0, <repo_root>)``.

This directory is INTENTIONALLY excluded from the production
``gpu_service`` / ``api`` / ``src`` runtime path. Nothing in the
production code path should import from this package.

Public surface (R2):
  - lib_config: load_config, gate_for_gpu_entry, ConfigGateError
  - lib_metrics: cer, rtf, realtime_speedup, code_metrics, bim_term_metrics,
    segment_metrics, Segment
  - lib_license_audit: collect_pkg_licenses, scan_models, render_markdown
  - lib_monitor: Monitor, MonitorConfig, bge_get_json, nvidia_smi_csv
"""
__all__ = [
    "lib_config",
    "lib_metrics",
    "lib_license_audit",
    "lib_monitor",
    "lib_runtime",
]
