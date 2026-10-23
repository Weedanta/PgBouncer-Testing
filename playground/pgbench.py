"""Build pgbench command lines (Tabel 3.5, 3.7) and parse what pgbench writes.

Parsing has two sources:
- stdout summary: TPS as pgbench reports it, latency average/stddev, connection time,
  failed transactions (used for the cross-check and for R4's connection figures);
- per-transaction logs (`--log`): the primary source for p50/p95/p99 (Subbab 3.5).
  Format (PG 14-18): client_id transaction_no time script_no time_epoch time_us
  [schedule_lag] [retries]; `time` is microseconds, or a word (failed, serialization,
  deadlock, skipped) for transactions that did not succeed.
"""

from __future__ import annotations

import math
import re
from array import array
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from playground.config import REPO_ROOT


@dataclass
class PgbenchSpec:
    """Everything that defines one pgbench invocation."""

    profile: str
    path: str
    host: str
    port: int
    dbname: str
    clients: int
    threads: int
    duration_s: int
    seed: int
    scale: int
    r5_vars: dict[str, int] = field(default_factory=dict)
    log_prefix: Path | None = None


def script_order(cfg: dict[str, Any], profile: str) -> list[str]:
    """Script names in -f order; pgbench's script_no is the index in this list."""
    prof = cfg["profiles"][profile]
    if "builtin" in prof:
        return [f"builtin:{prof['builtin']}"]
    return [Path(s).name for s in prof["scripts"]]


def build_command(cfg: dict[str, Any], spec: PgbenchSpec, repo_root: Path = REPO_ROOT) -> list[str]:
    pb = cfg["pgbench"]
    prof = cfg["profiles"][spec.profile]
    cmd = [
        "pgbench",
        "-h",
        spec.host,
        "-p",
        str(spec.port),
        "-U",
        "bench",
        "-c",
        str(spec.clients),
        "-j",
        str(max(1, min(spec.threads, spec.clients))),
        "-T",
        str(spec.duration_s),
        "-M",
        pb["protocol"],
        f"--random-seed={spec.seed}",
        f"--max-tries={pb['max_tries']}",
        f"--progress={pb['progress_s']}",
    ]
    if pb.get("failures_detailed"):
        cmd.append("--failures-detailed")
    if pb.get("no_vacuum"):
        cmd.append("-n")
    if prof["connect_per_transaction"]:
        cmd.append("-C")
    if "builtin" in prof:
        cmd += ["-b", prof["builtin"]]
    else:
        # pgbench does not detect the scale factor for custom scripts (:scale)
        cmd += ["-s", str(spec.scale)]
        for script, weight in prof["scripts"].items():
            cmd += ["-f", f"{repo_root / script}@{weight}"]
        for key, value in spec.r5_vars.items():
            cmd += ["-D", f"{key}={value}"]
    if spec.log_prefix is not None:
        cmd += ["--log", f"--log-prefix={spec.log_prefix}"]
    cmd.append(spec.dbname)
    return cmd


def r5_vars(cfg: dict[str, Any], dataset: str) -> dict[str, int]:
    seed = cfg["r5_seed"]
    frac = cfg["datasets"][dataset]["r5_seed_fraction"]
    return {
        "peserta": max(1, int(seed["peserta"] * frac)),
        "kompetisi": seed["kompetisi"],
        "soal": seed["soal_per_kompetisi"],
    }


# ---------------------------------------------------------------------------
# stdout summary
# ---------------------------------------------------------------------------
_PATTERNS = {
    "tx_processed": r"number of transactions actually processed:\s*(\d+)",
    "failed_tx": r"number of failed transactions:\s*(\d+)",
    "serialization_failures": r"number of serialization failures:\s*(\d+)",
    "deadlock_failures": r"number of deadlock failures:\s*(\d+)",
    "latency_avg_ms": r"latency average\s*=\s*([\d.]+)\s*ms",
    "latency_sd_ms": r"latency stddev\s*=\s*([\d.]+)\s*ms",
    "initial_conn_ms": r"initial connection time\s*=\s*([\d.]+)\s*ms",
    "avg_conn_ms": r"average connection time\s*=\s*([\d.]+)\s*ms",
    "tps_summary": r"tps\s*=\s*([\d.]+)",
    "clients": r"number of clients:\s*(\d+)",
    "threads": r"number of threads:\s*(\d+)",
}


def parse_summary(stdout: str, stderr: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, pat in _PATTERNS.items():
        m = re.search(pat, stdout)
        if m:
            out[key] = float(m.group(1)) if "." in m.group(1) else int(m.group(1))
    m = re.search(r"tps\s*=\s*[\d.]+\s*\(([^)]*)\)", stdout)
    out["tps_summary_note"] = m.group(1) if m else None
    text = stdout + "\n" + stderr
    out["aborted_clients"] = len(re.findall(r"client \d+ aborted", text))
    out["run_aborted"] = "Run was aborted" in text
    limit_errors = {
        "no_more_connections": len(re.findall(r"no more connections allowed", text)),
        "query_wait_timeout": len(re.findall(r"query_wait_timeout", text)),
        "too_many_clients": len(
            re.findall(r"too many clients already|sorry, too many clients", text)
        ),
    }
    out["limit_errors"] = limit_errors
    return out


# ---------------------------------------------------------------------------
# per-transaction logs
# ---------------------------------------------------------------------------
# Log-spaced histogram buckets: 1 us .. 1000 s, 200 buckets per decade (~1.2 % width).
HIST_EDGES_US = np.logspace(0, 9, 9 * 200 + 1)


@dataclass
class LatencyStats:
    n_ok: int
    n_failed: int
    n_skipped: int
    mean_ms: float
    sd_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    min_ms: float
    max_ms: float
    first_epoch: float | None
    last_epoch: float | None

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _stats(
    values_us: np.ndarray, n_failed: int, n_skipped: int, first: float | None, last: float | None
) -> LatencyStats:
    if values_us.size == 0:
        nan = float("nan")
        return LatencyStats(0, n_failed, n_skipped, nan, nan, nan, nan, nan, nan, nan, first, last)
    ms = values_us / 1000.0
    p50, p95, p99 = np.percentile(ms, [50, 95, 99])
    return LatencyStats(
        n_ok=int(ms.size),
        n_failed=n_failed,
        n_skipped=n_skipped,
        mean_ms=float(ms.mean()),
        sd_ms=float(ms.std(ddof=1)) if ms.size > 1 else 0.0,
        p50_ms=float(p50),
        p95_ms=float(p95),
        p99_ms=float(p99),
        min_ms=float(ms.min()),
        max_ms=float(ms.max()),
        first_epoch=first,
        last_epoch=last,
    )


@dataclass
class LogParseResult:
    overall: LatencyStats
    per_script: dict[int, LatencyStats]
    histogram: dict[int, np.ndarray]  # script_no -> counts per HIST_EDGES_US bucket
    failures_by_type: dict[str, int]
    files: list[str]


def parse_logs(files: list[Path]) -> LogParseResult:
    """Stream every per-thread log file; exact percentiles from successful transactions."""
    per_script_vals: dict[int, array] = {}
    failed: dict[int, int] = {}
    skipped: dict[int, int] = {}
    failure_types: dict[str, int] = {}
    first: float | None = None
    last: float | None = None
    for path in files:
        with open(path, encoding="ascii", errors="replace") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) < 6:
                    continue
                script = int(parts[3])
                epoch = int(parts[4]) + int(parts[5]) / 1e6
                if first is None or epoch < first:
                    first = epoch
                if last is None or epoch > last:
                    last = epoch
                t = parts[2]
                if t.isdigit():
                    per_script_vals.setdefault(script, array("q")).append(int(t))
                elif t == "skipped":
                    skipped[script] = skipped.get(script, 0) + 1
                else:
                    failed[script] = failed.get(script, 0) + 1
                    failure_types[t] = failure_types.get(t, 0) + 1
    scripts = sorted(set(per_script_vals) | set(failed) | set(skipped))
    per_script: dict[int, LatencyStats] = {}
    hist: dict[int, np.ndarray] = {}
    all_vals = []
    for s in scripts:
        vals = np.frombuffer(per_script_vals.get(s, array("q")), dtype=np.int64).astype(np.float64)
        all_vals.append(vals)
        per_script[s] = _stats(vals, failed.get(s, 0), skipped.get(s, 0), None, None)
        hist[s], _ = np.histogram(np.clip(vals, 1, None), bins=HIST_EDGES_US)
    merged = np.concatenate(all_vals) if all_vals else np.array([], dtype=np.float64)
    overall = _stats(merged, sum(failed.values()), sum(skipped.values()), first, last)
    return LogParseResult(overall, per_script, hist, failure_types, [p.name for p in files])


def percentile_from_hist(counts: np.ndarray, q: float, edges: np.ndarray = HIST_EDGES_US) -> float:
    """Approximate percentile (us) from a histogram: geometric middle of the bucket."""
    total = counts.sum()
    if total == 0:
        return math.nan
    target = q / 100.0 * total
    idx = int(np.searchsorted(np.cumsum(counts), target, side="left"))
    idx = min(idx, len(counts) - 1)
    return float(math.sqrt(edges[idx] * edges[idx + 1]))
