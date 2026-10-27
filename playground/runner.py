"""Campaign runner: one measured run, and the resumable campaign loop (Subbab 3.5).

One measured run ("satuan pengukuran"):
 1. fast preflight (lock intact, PostgreSQL/PgBouncer not restarted, disk space)
 2. reset the database if the profile writes data (or always, DEV-004)
 3. CHECKPOINT + statistics reset, "before" snapshot
 4. warmup: identical pgbench command, not recorded (design.warmup_s, 10 s in v5)
 5. measurement: pgbench with per-transaction logs (design.measure_s, 30 s), sampler running
 6. "after" snapshot and deltas; R5 invariants
 7. Prometheus export of the run window; CPU-saturation flags; RSS memory of PostgreSQL
    and PgBouncer (RM2 v5)
 8. parse logs -> TPS, mean, sd, p50/p95/p99 (+ per script for R5); compress logs
 9. append one row to runs.csv (append-only, checksum updated)

Nothing is silently re-run, dropped or overwritten. Every attempt goes to attempts.csv.
Infrastructure failures are retried at most once; system-under-test outcomes (failed
transactions, aborted clients, slow latency) are data and are never retried.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import zstandard

from playground import data, lock, prom
from playground.config import needs_reset, results_dir
from playground.pgbench import (
    PgbenchSpec,
    build_command,
    parse_logs,
    parse_summary,
    r5_vars,
    script_order,
)
from playground.plan import PlanRow, plan_from_config, read_plan, write_plan
from playground.settings import Endpoints, cpuset_size, pgbench_threads, prometheus_required
from playground.snapshots import Sampler, deltas, snapshot

RUN_FIELDS = [
    "seq",
    "run_id",
    "round",
    "profile",
    "concurrency",
    "path",
    "pair_id",
    "pgbench_seed",
    "dataset",
    "start_utc",
    "end_utc",
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
    "flag_client_cpu_saturated",
    "flag_pgbouncer_cpu_saturated",
    "flag_aborted_clients",
    "flag_failed_tx",
    "flag_checkpoint_during_run",
    "flag_log_count_mismatch",
    "flag_invariant_failed",
    "flag_container_restarted",
    "flag_prometheus_missing",
    "locked_inputs_sha256",
    "smoke",
]
ATTEMPT_FIELDS = ["seq", "run_id", "attempt", "started_utc", "ended_utc", "outcome", "message"]


class InfraError(RuntimeError):
    """The instrument failed (not the system under test). Retried at most once."""


def now() -> str:
    return datetime.now(UTC).isoformat()


def log(msg: str) -> None:
    print(f"[{datetime.now(UTC).strftime('%H:%M:%S')}] {msg}", flush=True)


def running_pgbench() -> list[int]:
    """PIDs of pgbench processes visible to this container (orphans of a killed runner)."""
    pids = []
    for proc in Path("/proc").iterdir():
        if proc.name.isdigit():
            try:
                if (proc / "comm").read_text().strip() == "pgbench":
                    pids.append(int(proc.name))
            except OSError:
                continue
    return pids


def wait_for_orphans(timeout_s: float) -> list[int]:
    """Wait until no pgbench is running (an orphan would load the system under test)."""
    deadline = time.monotonic() + timeout_s
    while running_pgbench() and time.monotonic() < deadline:
        time.sleep(2)
    return running_pgbench()


# ---------------------------------------------------------------------------
# append-only CSV with checksum
# ---------------------------------------------------------------------------
def append_row(path: Path, fields: list[str], row: dict[str, Any]) -> None:
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        if new:
            writer.writeheader()
        writer.writerow(row)
        fh.flush()
        os.fsync(fh.fileno())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_name(path.name + ".sha256").write_text(f"{digest}  {path.name}\n", encoding="utf-8")


def verify_append_only(path: Path) -> None:
    digest_file = path.with_name(path.name + ".sha256")
    if not (path.exists() and digest_file.exists()):
        return
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest_file.read_text().split()[0]:
        raise RuntimeError(f"{path} changed outside the runner (checksum mismatch)")


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# ---------------------------------------------------------------------------
# one measurement (shared by campaign, smoke and calibration)
# ---------------------------------------------------------------------------
@dataclass
class RunRequest:
    run_dir: Path
    profile: str
    concurrency: int
    path: str
    seed: int
    dataset: str
    warmup_s: int
    measure_s: int
    do_reset: bool
    keep_logs: bool = True
    label: str = ""


def _pgbench(
    cmd: list[str], env: dict[str, str], stdout: Path, stderr: Path, timeout_s: float
) -> int:
    with open(stdout, "w", encoding="utf-8") as out, open(stderr, "w", encoding="utf-8") as err:
        out.write("# " + " ".join(cmd) + "\n")
        out.flush()
        try:
            proc = subprocess.run(
                cmd, env=env, stdout=out, stderr=err, timeout=timeout_s, check=False
            )
        except subprocess.TimeoutExpired as exc:
            raise InfraError(f"pgbench did not finish within {timeout_s:.0f} s") from exc
    return proc.returncode


def execute(cfg: dict[str, Any], ep: Endpoints, req: RunRequest) -> dict[str, Any]:
    """Run warmup + measurement once and return the summary (does not touch runs.csv)."""
    rd = req.run_dir
    rd.mkdir(parents=True, exist_ok=False)
    dbname = cfg["datasets"][req.dataset]["database"]
    scale = cfg["datasets"][req.dataset]["scale"]
    host, port = ep.host_port(req.path)
    env = dict(os.environ)
    env["PGPASSWORD"] = data.db.secret(ep, "bench_password")
    threads = pgbench_threads(cfg["pgbench"]["threads"], req.concurrency)
    vars_ = r5_vars(cfg, req.dataset) if req.profile == "R5" else {}
    meta: dict[str, Any] = {
        "label": req.label,
        "profile": req.profile,
        "concurrency": req.concurrency,
        "path": req.path,
        "dataset": req.dataset,
        "database": dbname,
        "scale": scale,
        "seed": req.seed,
        "threads": threads,
        "warmup_s": req.warmup_s,
        "measure_s": req.measure_s,
        "script_order": script_order(cfg, req.profile),
        "r5_vars": vars_,
        "endpoint": f"{host}:{port}",
    }

    reset_s = None
    if req.do_reset:
        try:
            reset_s = data.reset(cfg, ep, req.dataset, req.profile)
        except Exception as exc:  # noqa: BLE001 - any reset failure is an infra failure
            raise InfraError(f"reset failed: {exc}") from exc
    meta["reset_s"] = reset_s
    try:
        data.pre_run(ep, dbname)
        before = snapshot(ep, dbname, include_config=True)
    except Exception as exc:  # noqa: BLE001
        raise InfraError(f"pre-run failed: {exc}") from exc
    (rd / "snapshot_before.json").write_text(json.dumps(before, indent=2, default=str))

    spec = PgbenchSpec(
        req.profile,
        req.path,
        host,
        port,
        dbname,
        req.concurrency,
        threads,
        req.warmup_s,
        req.seed,
        scale,
        vars_,
    )
    warm_cmd = build_command(cfg, spec)
    meta["warmup_cmd"] = warm_cmd
    meta["warmup_started_utc"] = now()
    warm_rc = _pgbench(
        warm_cmd, env, rd / "warmup.stdout", rd / "warmup.stderr", req.warmup_s + 600
    )
    meta["warmup_rc"] = warm_rc
    if warm_rc == 1:
        raise InfraError(f"warmup pgbench setup error: {(rd / 'warmup.stderr').read_text()[-500:]}")

    # Deltas must cover the measurement window only, so take a snapshot after warmup.
    measure_start = snapshot(ep, dbname)
    (rd / "snapshot_measure_start.json").write_text(
        json.dumps(measure_start, indent=2, default=str)
    )
    spec.duration_s = req.measure_s
    spec.log_prefix = rd / "pgbench_log"
    cmd = build_command(cfg, spec)
    meta["measure_cmd"] = cmd
    sampler = Sampler(ep, dbname, rd / "samples.csv", cfg["monitoring"]["sampler_interval_s"])
    sampler.start()
    t0 = time.time()
    meta["start_utc"] = now()
    try:
        rc = _pgbench(cmd, env, rd / "measure.stdout", rd / "measure.stderr", req.measure_s + 900)
    finally:
        t1 = time.time()
        meta["end_utc"] = now()
        sampler.stop()
    meta["pgbench_rc"] = rc
    if rc == 1:
        raise InfraError(f"pgbench setup error: {(rd / 'measure.stderr').read_text()[-500:]}")

    after = snapshot(ep, dbname)
    (rd / "snapshot_after.json").write_text(json.dumps(after, indent=2, default=str))
    dl = deltas(measure_start, after, dbname)
    dl["postmaster_restarted"] = before.get("postmaster_start") != after.get("postmaster_start")
    meta["deltas"] = dl

    if req.profile == "R5":
        inv = data.r5_invariants(ep, dbname)
        meta["r5_invariants"] = inv
        (rd / "invariants.json").write_text(json.dumps(inv, indent=2, default=str))

    # Prometheus export of the measurement window (+ one scrape on each side)
    prom_df = None
    if prom.available(ep.prometheus_url):
        prom_df = prom.export_window(
            ep.prometheus_url, dbname, t0 - 5, t1 + 5, rd / "prometheus.parquet"
        )
    elif prometheus_required():
        raise InfraError(f"Prometheus not reachable at {ep.prometheus_url}")
    meta["prometheus"] = prom_df is not None

    summary = parse_summary(
        (rd / "measure.stdout").read_text(), (rd / "measure.stderr").read_text()
    )
    meta["pgbench_summary"] = summary
    logs = sorted(rd.glob("pgbench_log*"))
    parsed = parse_logs(logs)
    scripts = script_order(cfg, req.profile)
    per_script = []
    for idx, st in parsed.per_script.items():
        row = {"script_no": idx, "script": scripts[idx] if idx < len(scripts) else str(idx)}
        row.update(st.as_dict())
        per_script.append(row)
    pd.DataFrame(per_script).to_csv(rd / "latency_per_script.csv", index=False)
    hist_rows = []
    for idx, counts in parsed.histogram.items():
        nz = counts.nonzero()[0]
        hist_rows += [{"script_no": idx, "bucket": int(b), "count": int(counts[b])} for b in nz]
    pd.DataFrame(hist_rows, columns=["script_no", "bucket", "count"]).to_parquet(
        rd / "latency_hist.parquet", index=False
    )
    meta["log_failures_by_type"] = parsed.failures_by_type
    _compress_logs(logs, rd, keep=req.keep_logs)

    ov = parsed.overall
    # Primary TPS = pgbench's own figure (Subbab 2.6). tps_log is recomputed from the
    # per-transaction log; the log must contain exactly the transactions pgbench counted.
    tps_log = ov.n_ok / req.measure_s if req.measure_s else float("nan")
    samples = pd.read_csv(rd / "samples.csv")
    result: dict[str, Any] = {
        "dataset": req.dataset,
        "start_utc": meta["start_utc"],
        "end_utc": meta["end_utc"],
        "measure_s": req.measure_s,
        "tps": summary.get("tps_summary"),
        "tps_log": tps_log,
        "tx_processed": summary.get("tx_processed"),
        "lat_mean_ms": ov.mean_ms,
        "lat_sd_ms": ov.sd_ms,
        "p50_ms": ov.p50_ms,
        "p95_ms": ov.p95_ms,
        "p99_ms": ov.p99_ms,
        "lat_min_ms": ov.min_ms,
        "lat_max_ms": ov.max_ms,
        "n_ok": ov.n_ok,
        "failed_tx": max(ov.n_failed, int(summary.get("failed_tx", 0) or 0)),
        "aborted_clients": summary.get("aborted_clients", 0),
        "pgbench_rc": rc,
        "initial_conn_ms": summary.get("initial_conn_ms"),
        "avg_conn_ms": summary.get("avg_conn_ms"),
        "reset_s": reset_s,
        "pg_backends_max": _max(samples, "pg_total"),
        "pg_backends_mean": _mean(samples, "pg_total"),
        "sv_active_max": _max(samples, "sv_active"),
        "sv_total_max": _max_sum(samples, ["sv_active", "sv_idle", "sv_used"]),
        "cl_waiting_max": _max(samples, "cl_waiting"),
        "pgb_avg_wait_ms": (dl["pgb_avg_wait_us"] / 1000.0)
        if dl.get("pgb_avg_wait_us") is not None
        else None,
        "checkpoints_during_run": dl.get("checkpoints"),
        "cache_hit_ratio": dl.get("pg_cache_hit_ratio"),
        # Tabel 3.8 (v4): deadlocks and sessions waiting for a lock, per run (Subbab 3.5.2)
        "deadlocks": dl.get("pg_deadlocks"),
        "lock_waiting_mean": _mean(samples, "pg_lock_waiting"),
        "lock_waiting_max": _max(samples, "pg_lock_waiting"),
        "limit_errors": summary.get("limit_errors"),
    }
    cpu = {}
    if prom_df is not None:
        for name in ("client", "pgbouncer", "postgres"):
            cpu[name] = prom.container_cpu(prom_df, f"pgb-{name}")["mean"]
    result["client_cpu_mean"] = cpu.get("client")
    result["pgbouncer_cpu_mean"] = cpu.get("pgbouncer")
    result["postgres_cpu_mean"] = cpu.get("postgres")
    # RM2 v5: private memory (RSS) of PostgreSQL and PgBouncer during the measurement, the
    # input of the per-connection memory and capacity analysis (done outside this repo).
    for name in ("postgres", "pgbouncer"):
        rss = (
            prom.container_mean(prom_df, "mem_rss_bytes_by_container", f"pgb-{name}")
            if prom_df is not None
            else None
        )
        result[f"{name}_rss_mb_mean"] = rss / 2**20 if rss is not None else None
    # Evidence that the dataset stays in memory after warmup (Subbab 3.5.2, v4).
    disk = prom.series_mean(prom_df, "disk_read_bytes_rate") if prom_df is not None else None
    result["disk_read_mb_s_mean"] = disk / 1e6 if disk is not None else None
    result.update(compute_flags(cfg, result, dl, meta))
    meta["result"] = result
    (rd / "meta.json").write_text(json.dumps(meta, indent=2, default=str))
    return result


def _mean(df: pd.DataFrame, col: str) -> float | None:
    if col not in df or df[col].dropna().empty:
        return None
    return float(pd.to_numeric(df[col], errors="coerce").mean())


def _max(df: pd.DataFrame, col: str) -> float | None:
    if col not in df or df[col].dropna().empty:
        return None
    return float(pd.to_numeric(df[col], errors="coerce").max())


def _max_sum(df: pd.DataFrame, cols: list[str]) -> float | None:
    if not all(c in df for c in cols) or df[cols].dropna(how="all").empty:
        return None
    return float(df[cols].apply(pd.to_numeric, errors="coerce").fillna(0).sum(axis=1).max())


def _compress_logs(logs: list[Path], rd: Path, keep: bool) -> None:
    if keep:
        cctx = zstandard.ZstdCompressor(level=10)
        for path in logs:
            with open(path, "rb") as src, open(rd / (path.name + ".zst"), "wb") as dst:
                cctx.copy_stream(src, dst)
    for path in logs:
        path.unlink()


def compute_flags(
    cfg: dict[str, Any], r: dict[str, Any], dl: dict[str, Any], meta: dict[str, Any]
) -> dict[str, Any]:
    """Recorded, never used to exclude runs automatically (Subbab 3.8)."""
    fl = cfg["flags"]
    client_cpus = cpuset_size("PLAYGROUND_CPUSET_CLIENT")
    flags: dict[str, Any] = {}
    flags["flag_client_cpu_saturated"] = (
        None
        if r["client_cpu_mean"] is None or not client_cpus
        else r["client_cpu_mean"] >= fl["client_cpu_saturation"] * client_cpus
    )
    flags["flag_pgbouncer_cpu_saturated"] = (
        None
        if r["pgbouncer_cpu_mean"] is None
        else r["pgbouncer_cpu_mean"] >= fl["pgbouncer_cpu_saturation"] * 1.0
    )
    flags["flag_aborted_clients"] = bool(r["aborted_clients"])
    flags["flag_failed_tx"] = bool(r["failed_tx"])
    ck = r["checkpoints_during_run"]
    flags["flag_checkpoint_during_run"] = None if ck is None else ck > 0
    tx = r["tx_processed"]
    flags["flag_log_count_mismatch"] = (
        None if tx is None else abs(r["n_ok"] - tx) > fl["log_count_tolerance"]
    )
    inv = meta.get("r5_invariants")
    flags["flag_invariant_failed"] = None if inv is None else not all(i["ok"] for i in inv)
    flags["flag_container_restarted"] = bool(dl.get("postmaster_restarted"))
    flags["flag_prometheus_missing"] = not meta.get("prometheus", False)
    return flags


# ---------------------------------------------------------------------------
# campaign
# ---------------------------------------------------------------------------
class Campaign:
    def __init__(self, cfg: dict[str, Any], ep: Endpoints, campaign_id: str, smoke: bool) -> None:
        self.cfg, self.ep, self.id, self.smoke = cfg, ep, campaign_id, smoke
        self.dir = results_dir() / campaign_id
        self.plan_path = self.dir / "plan.csv"
        self.runs_path = self.dir / "runs.csv"
        self.attempts_path = self.dir / "attempts.csv"
        self.incidents = self.dir / "incidents.log"
        self.dataset = "smoke" if smoke else "campaign"
        d = cfg["design"]
        self.warmup_s = cfg["smoke"]["warmup_s"] if smoke else d["warmup_s"]
        self.measure_s = cfg["smoke"]["measure_s"] if smoke else d["measure_s"]

    # -- setup ---------------------------------------------------------------
    def create(self) -> list[PlanRow]:
        if self.plan_path.exists():
            raise RuntimeError(f"campaign {self.id} already exists; use `resume`")
        self.dir.mkdir(parents=True, exist_ok=False)
        rows = plan_from_config(self.cfg, self.smoke)
        digest = write_plan(rows, self.plan_path)
        lock_info = lock.read_lock()
        info = {
            "campaign_id": self.id,
            "smoke": self.smoke,
            "created_utc": now(),
            "plan_sha256": digest,
            "n_runs": len(rows),
            "dataset": self.dataset,
            "warmup_s": self.warmup_s,
            "measure_s": self.measure_s,
            "locked_inputs_sha256": lock_info["inputs_sha256"] if lock_info else None,
            "experiment": self.cfg,
            "host_env": {k: v for k, v in os.environ.items() if k.startswith("PLAYGROUND_")},
        }
        (self.dir / "campaign.json").write_text(json.dumps(info, indent=2, default=str))
        return rows

    def info(self) -> dict[str, Any]:
        return json.loads((self.dir / "campaign.json").read_text())

    def done_seqs(self) -> set[int]:
        return {int(r["seq"]) for r in read_csv(self.runs_path)}

    def remaining(self) -> list[PlanRow]:
        done = self.done_seqs()
        return [r for r in read_plan(self.plan_path) if r.seq not in done]

    def incident(self, msg: str) -> None:
        with open(self.incidents, "a", encoding="utf-8") as fh:
            fh.write(f"{now()} {msg}\n")
        log(f"INCIDENT: {msg}")

    # -- checks ----------------------------------------------------------------
    def full_preflight(self) -> list[str]:
        problems = self.fast_preflight()
        pf = self.cfg["preflight"]
        load1 = os.getloadavg()[0]
        ncpu = os.cpu_count() or 1
        if load1 / ncpu > pf["max_load_avg_per_cpu"]:
            problems.append(
                f"host load average {load1:.1f} on {ncpu} CPUs is too high: "
                "stop other workloads and wait"
            )
        if not data.dataset_ready(self.ep, self.cfg, self.dataset):
            problems.append(f"dataset '{self.dataset}' is not initialized (make init-data)")
        if prometheus_required() and not prom.available(self.ep.prometheus_url):
            problems.append("Prometheus is not reachable")
        return problems

    def fast_preflight(self) -> list[str]:
        problems = []
        if not self.smoke:
            problems += lock.verify(self.ep)
            ref = self.info().get("locked_inputs_sha256")
            current = lock.read_lock()
            if current and ref and current["inputs_sha256"] != ref:
                problems.append("the lock changed since this campaign was created")
        free_gb = shutil.disk_usage(results_dir()).free / 1e9
        if free_gb < self.cfg["preflight"]["min_free_disk_gb"] and not self.smoke:
            problems.append(f"only {free_gb:.0f} GB free on the results disk")
        verify_append_only(self.runs_path)
        verify_append_only(self.attempts_path)
        return problems

    # -- loop --------------------------------------------------------------------
    def attempts_for(self, seq: int) -> list[dict[str, str]]:
        return [a for a in read_csv(self.attempts_path) if int(a["seq"]) == seq]

    def run(self, max_runs: int | None = None) -> None:
        todo = self.remaining()
        if not todo:
            log("campaign complete: nothing left to run")
            return
        # attempts that started but never ended were interrupted
        orphans = running_pgbench()
        if orphans:
            self.incident(
                f"pgbench still running from an earlier attempt (pids {orphans}); waiting"
            )
            left = wait_for_orphans(self.warmup_s + self.measure_s + 120)
            if left:
                raise RuntimeError(f"pgbench processes {left} are still running; stop them first")
        attempts = read_csv(self.attempts_path)
        finished = {(b["seq"], b["attempt"]) for b in attempts if b["outcome"] != "started"}
        for a in attempts:
            if a["outcome"] == "started" and (a["seq"], a["attempt"]) not in finished:
                append_row(
                    self.attempts_path,
                    ATTEMPT_FIELDS,
                    {
                        **a,
                        "ended_utc": now(),
                        "outcome": "interrupted",
                        "message": "found unfinished on resume",
                    },
                )
                self.incident(f"run {a['run_id']} attempt {a['attempt']} was interrupted")
        info = self.info()
        pm_ref = info.get("postmaster_start")
        with data.db.superuser(self.ep) as conn:
            pm_now = str(data.db.rows(conn, "SELECT pg_postmaster_start_time() AS t")[0]["t"])
        if pm_ref is None:
            info["postmaster_start"] = pm_now
            (self.dir / "campaign.json").write_text(json.dumps(info, indent=2, default=str))
        elif pm_ref != pm_now:
            self.incident(f"PostgreSQL restarted since the campaign started ({pm_ref} -> {pm_now})")
        durations: list[float] = []
        total = len(read_plan(self.plan_path))
        for n, row in enumerate(todo):
            if max_runs is not None and n >= max_runs:
                break
            problems = self.fast_preflight()
            if problems:
                raise RuntimeError("preflight failed:\n  - " + "\n  - ".join(problems))
            t0 = time.monotonic()
            self.run_one(row, pm_now)
            durations.append(time.monotonic() - t0)
            avg = sum(durations[-10:]) / len(durations[-10:])
            left = len(todo) - n - 1
            log(
                f"{row.seq}/{total} done ({row.profile} c={row.concurrency} {row.path}); "
                f"ETA {left * avg / 3600:.1f} h for {left} runs"
            )

    def run_one(self, row: PlanRow, pm_ref: str) -> None:
        retries = self.cfg["retry"]["infra_max_retries"]
        infra_failures = 0
        while True:
            attempt = len({a["attempt"] for a in self.attempts_for(row.seq)}) + 1
            run_dir = self.dir / "runs" / row.run_id
            if run_dir.exists():  # leftover of an interrupted/failed attempt: keep it
                run_dir.rename(run_dir.with_name(f"{row.run_id}.attempt{attempt - 1}"))
            base = {"seq": row.seq, "run_id": row.run_id, "attempt": attempt, "started_utc": now()}
            append_row(self.attempts_path, ATTEMPT_FIELDS, {**base, "outcome": "started"})
            log(
                f"run {row.run_id} (attempt {attempt}) round {row.round} {row.profile} "
                f"c={row.concurrency} path={row.path}"
            )
            req = RunRequest(
                run_dir=run_dir,
                profile=row.profile,
                concurrency=row.concurrency,
                path=row.path,
                seed=row.pgbench_seed,
                dataset=self.dataset,
                warmup_s=self.warmup_s,
                measure_s=self.measure_s,
                do_reset=needs_reset(self.cfg, row.profile),
                keep_logs=self.cfg["raw_logs"]["keep"],
                label=self.id,
            )
            try:
                result = execute(self.cfg, self.ep, req)
            except InfraError as exc:
                append_row(
                    self.attempts_path,
                    ATTEMPT_FIELDS,
                    {
                        **base,
                        "ended_utc": now(),
                        "outcome": "infra_error",
                        "message": str(exc)[:500],
                    },
                )
                infra_failures += 1
                if infra_failures > retries:
                    raise RuntimeError(
                        f"run {row.run_id} failed twice for infrastructure reasons; campaign "
                        f"halted. Fix the cause, then `make resume`. Last error: {exc}"
                    ) from exc
                self.incident(f"infra error in {row.run_id}, retrying once: {exc}")
                continue
            except KeyboardInterrupt:
                append_row(
                    self.attempts_path,
                    ATTEMPT_FIELDS,
                    {
                        **base,
                        "ended_utc": now(),
                        "outcome": "interrupted",
                        "message": "KeyboardInterrupt",
                    },
                )
                raise
            if result.get("flag_container_restarted"):
                self.incident(f"PostgreSQL restarted during {row.run_id}")
            outcome = "ok" if not result["aborted_clients"] else "sut_aborted_clients"
            append_row(
                self.attempts_path,
                ATTEMPT_FIELDS,
                {**base, "ended_utc": now(), "outcome": outcome, "message": ""},
            )
            lock_info = lock.read_lock()
            row_out = {
                "seq": row.seq,
                "run_id": row.run_id,
                "round": row.round,
                "profile": row.profile,
                "concurrency": row.concurrency,
                "path": row.path,
                "pair_id": row.pair_id,
                "pgbench_seed": row.pgbench_seed,
                "locked_inputs_sha256": lock_info["inputs_sha256"] if lock_info else "",
                "smoke": self.smoke,
                **result,
            }
            append_row(self.runs_path, RUN_FIELDS, row_out)
            return

    def status(self) -> dict[str, Any]:
        plan = read_plan(self.plan_path)
        runs = read_csv(self.runs_path)
        attempts = read_csv(self.attempts_path)
        flag_keys = [
            k for k in RUN_FIELDS if k.startswith("flag_") and k != "flag_prometheus_missing"
        ]
        flagged = [r for r in runs if any(r.get(k) == "True" for k in flag_keys)]
        return {
            "campaign": self.id,
            "smoke": self.smoke,
            "planned": len(plan),
            "done": len(runs),
            "attempts": len([a for a in attempts if a["outcome"] != "started"]),
            "infra_errors": len([a for a in attempts if a["outcome"] == "infra_error"]),
            "interrupted": len([a for a in attempts if a["outcome"] == "interrupted"]),
            "flagged_runs": len(flagged),
            "next": self.remaining()[0].run_id if self.remaining() else None,
        }

    def expected_hours(self, n_runs: int) -> float:
        # + about 25 s per run for the reset (write profiles), snapshots, parsing and export;
        # an estimate until the first runs give a real average (the ETA uses those)
        per_run = self.warmup_s + self.measure_s + 25
        return n_runs * per_run / 3600
