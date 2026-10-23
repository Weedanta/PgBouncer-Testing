"""Read a campaign's raw outputs (runs.csv, run directories). Never writes to them."""

from __future__ import annotations

import hashlib
import json
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
            raise RuntimeError(f"{path} does not match its checksum: refusing to analyze")


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


def campaign_info(campaign_dir: Path) -> dict:
    return json.loads((campaign_dir / "campaign.json").read_text(encoding="utf-8"))


def load_samples(campaign_dir: Path, runs: pd.DataFrame) -> pd.DataFrame:
    """Per-run summary of the sampler (mean during the measurement window)."""
    rows = []
    for _, r in runs.iterrows():
        path = campaign_dir / "runs" / r["run_id"] / "samples.csv"
        if not path.exists():
            continue
        s = pd.read_csv(path)
        out = {"run_id": r["run_id"]}
        for col in ("pg_total", "pg_active", "cl_active", "cl_waiting", "sv_active", "sv_idle"):
            if col in s:
                vals = pd.to_numeric(s[col], errors="coerce")
                out[f"{col}_mean"] = vals.mean()
                out[f"{col}_max"] = vals.max()
        rows.append(out)
    return pd.DataFrame(rows)


def _short_cmd(cmd: list[str]) -> str:
    """The pgbench command as run, with container paths shortened to workloads/..."""
    out = []
    for part in cmd:
        if "/workloads/" in part:
            prefix = "-f " if part.startswith("-f ") else ""
            part = prefix + "workloads/" + part.split("/workloads/", 1)[1]
        out.append(part)
    return " ".join(out)


def load_commands(campaign_dir: Path, runs: pd.DataFrame) -> pd.DataFrame:
    """pgbench command, target port and scripts of every measurement unit of the first
    round (Bab 4 v4: "perintah pgbench, port tujuan, dan skrip profil request"). Other
    rounds differ only in --random-seed and --log-prefix (see each run's meta.json)."""
    if runs.empty:
        return pd.DataFrame()
    first = runs[runs["round"] == runs["round"].min()]
    rows = []
    for _, r in first.sort_values(["profile", "concurrency", "path"]).iterrows():
        path = campaign_dir / "runs" / r["run_id"] / "meta.json"
        if not path.exists():
            continue
        cmd = json.loads(path.read_text(encoding="utf-8")).get("measure_cmd") or []
        opt = {cmd[i]: cmd[i + 1] for i in range(len(cmd) - 1) if cmd[i].startswith("-")}
        scripts = [cmd[i + 1].split("/workloads/")[-1] for i, c in enumerate(cmd) if c == "-f"]
        rows.append(
            {
                "profile": r["profile"],
                "concurrency": r["concurrency"],
                "path": r["path"],
                "port": opt.get("-p"),
                "threads_j": opt.get("-j"),
                "builtin": opt.get("-b", ""),
                "scripts": "; ".join("workloads/" + s for s in scripts),
                "connect_per_tx": "-C" in cmd,
                "command": _short_cmd(cmd),
            }
        )
    return pd.DataFrame(rows)


def load_prometheus(campaign_dir: Path, runs: pd.DataFrame) -> pd.DataFrame:
    """Per-run means of the Prometheus series (CPU and memory per container, I/O, ...)."""
    rows = []
    for _, r in runs.iterrows():
        path = campaign_dir / "runs" / r["run_id"] / "prometheus.parquet"
        if not path.exists():
            continue
        p = pd.read_parquet(path)
        p = p.dropna(subset=["value"])
        out = {"run_id": r["run_id"]}
        for (series, labels), g in p.groupby(["series", "labels"]):
            lab = json.loads(labels) if labels else {}
            suffix = lab.get("name") or lab.get("state") or ""
            key = f"{series}:{suffix}" if suffix else series
            out[key] = g["value"].mean()
        rows.append(out)
    return pd.DataFrame(rows)


def load_script_latency(campaign_dir: Path, runs: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for _, r in runs[runs["profile"] == "R5"].iterrows():
        path = campaign_dir / "runs" / r["run_id"] / "latency_per_script.csv"
        if path.exists():
            s = pd.read_csv(path)
            s["run_id"], s["round"] = r["run_id"], r["round"]
            s["concurrency"], s["path"] = r["concurrency"], r["path"]
            frames.append(s)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
