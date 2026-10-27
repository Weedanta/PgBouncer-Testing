"""Read a campaign's runs.csv (one row per measured run). Never writes to it.

runs.csv is the measurement data of the campaign. Statistics are done outside this
repository; inside it, runs.csv is only read by `playground evidence`.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd

NUMERIC = [
    "seq",
    "round",
    "concurrency",
    "pgbench_seed",
    "measure_s",
    "tps",
    "tps_log",
    "tx_processed",
    "lat_mean_ms",
    "lat_sd_ms",
    "p50_ms",
    "p95_ms",
    "p99_ms",
    "lat_min_ms",
    "lat_max_ms",
    "n_ok",
    "failed_tx",
    "aborted_clients",
    "pgbench_rc",
    "initial_conn_ms",
    "avg_conn_ms",
    "reset_s",
    "pg_backends_max",
    "sv_active_max",
    "sv_total_max",
    "cl_waiting_max",
    "pgb_avg_wait_ms",
    "checkpoints_during_run",
    "cache_hit_ratio",
    "disk_read_mb_s_mean",
    "deadlocks",
    "lock_waiting_mean",
    "lock_waiting_max",
    "client_cpu_mean",
    "pgbouncer_cpu_mean",
    "postgres_cpu_mean",
    "pg_backends_mean",
    "postgres_rss_mb_mean",
    "pgbouncer_rss_mb_mean",
]


def verify_checksum(path: Path) -> None:
    digest_file = path.with_name(path.name + ".sha256")
    if digest_file.exists():
        want = digest_file.read_text().split()[0]
        if hashlib.sha256(path.read_bytes()).hexdigest() != want:
            raise RuntimeError(f"{path} does not match its checksum: refusing to read it")


def load_runs(campaign_dir: Path) -> pd.DataFrame:
    path = campaign_dir / "runs.csv"
    verify_checksum(path)
    df = pd.read_csv(path, dtype={"run_id": str, "pair_id": str, "profile": str, "path": str})
    for col in NUMERIC:
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in [c for c in df.columns if c.startswith("flag_")]:
        df[col] = df[col].map({True: True, False: False, "True": True, "False": False})
    df["flagged"] = (
        df[[c for c in df.columns if c.startswith("flag_") and c != "flag_prometheus_missing"]]
        .fillna(False)
        .any(axis=1)
    )
    return df
