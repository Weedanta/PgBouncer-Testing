"""RM1: setting the PgBouncer configuration (Subbab 3.3, Gambar 3.2).

Step A, documentation and literature: compute the initial values from the rules of
Tabel 3.2 and check every workload against Tabel 2.2 (session-feature linter).

Step B, "pengamatan awal": short 30 s runs at c = 100 and c = 1000 (two levels far above
the pool size; Subbab 3.3.2 v5), through PgBouncer, checking four things:
  1. every profile R1-R5 runs without errors in transaction mode;
  2. pg_backend_pid() changes between transactions of the same client (backends rotate);
  3. no transaction fails on a connection limit or the queue timeout;
  4. the rule's pool size is not in a clearly bad region, compared with half and twice
     the value (D3 criterion).

Step C: if a check fails, the offending value may be adjusted ONCE with a written
reason (`playground adjust`), and all checks run again. Calibration data is never
used as performance data.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np
import psycopg
import yaml

from playground import db, lock
from playground.config import REPO_ROOT, results_dir
from playground.lint import lint_experiment, report_markdown
from playground.pgbench import PgbenchSpec, build_command
from playground.render import ADJUSTABLE, OVERRIDES, load_overrides, pool_size, rule_pool_size
from playground.runner import RunRequest, execute, log
from playground.settings import Endpoints, pgbench_threads

LIMIT_PATTERNS = re.compile(
    r"no more connections allowed|query_wait_timeout|max_client_conn|server login failed",
    re.I,
)


def _set_pool(ep: Endpoints, dbname: str, size: int) -> None:
    with db.pgb_admin(ep) as admin:
        db.admin_command(admin, f"SET default_pool_size = {size}")
        db.admin_command(admin, f"SET max_db_connections = {size}")
        db.admin_command(admin, f"KILL {dbname}")
        db.admin_command(admin, f"RESUME {dbname}")


def _restore_pool(ep: Endpoints, dbname: str) -> dict[str, str]:
    """RELOAD re-reads pgbouncer.ini, undoing every SET made during calibration."""
    with db.pgb_admin(ep) as admin:
        db.admin_command(admin, "RELOAD")
        try:
            db.admin_command(admin, f"KILL {dbname}")
            db.admin_command(admin, f"RESUME {dbname}")
        except psycopg.Error:
            pass
        return {r["key"]: r["value"] for r in db.admin_rows(admin, "SHOW CONFIG")}


def _log_offset(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return -1


def _log_matches(path: Path, offset: int) -> list[str] | None:
    if offset < 0 or not path.exists():
        return None
    with open(path, encoding="utf-8", errors="replace") as fh:
        fh.seek(offset)
        return [line.strip() for line in fh if LIMIT_PATTERNS.search(line)]


def check_rotation(
    cfg: dict[str, Any], ep: Endpoints, dataset: str, n: int, bg_clients: int
) -> dict[str, Any]:
    """Check 2: same client, separate transactions, through PgBouncer vs direct."""
    dbname = cfg["datasets"][dataset]["database"]
    host, port = ep.host_port("pgbouncer")
    env = dict(os.environ)
    env["PGPASSWORD"] = db.secret(ep, "bench_password")
    spec = PgbenchSpec(
        "R3",
        "pgbouncer",
        host,
        port,
        dbname,
        bg_clients,
        pgbench_threads(cfg["pgbench"]["threads"], bg_clients),
        40,
        1,
        cfg["datasets"][dataset]["scale"],
    )
    bg = subprocess.Popen(
        build_command(cfg, spec), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        time.sleep(5)
        out: dict[str, Any] = {"background": f"R3 c={bg_clients} melalui PgBouncer"}
        for path in ("pgbouncer", "direct"):
            pids = []
            with db.bench(ep, path, dbname) as conn:
                for _ in range(n):
                    pids.append(conn.execute("SELECT pg_backend_pid()").fetchone()[0])
            out[path] = {"transactions": n, "distinct_pids": len(set(pids)), "pids": pids}
    finally:
        bg.terminate()
        bg.wait(timeout=60)
    out["passed"] = out["pgbouncer"]["distinct_pids"] > 1 and out["direct"]["distinct_pids"] == 1
    return out


def run_calibration(
    cfg: dict[str, Any],
    ep: Endpoints,
    host_env: dict[str, str],
    dataset: str = "campaign",
    quick: bool = False,
) -> Path:
    cal = cfg["calibration"]
    dbname = cfg["datasets"][dataset]["database"]
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = results_dir() / "calibration" / (f"{ts}-SMOKE" if dataset == "smoke" else ts)
    out.mkdir(parents=True, exist_ok=False)
    concurrency = cfg["smoke"]["concurrency"] if dataset == "smoke" else cal["concurrency"]
    duration = 10 if quick else cal["duration_s"]
    warmup = 3 if quick else cal["warmup_s"]
    reps = 1 if quick else cal["pool_size_repetitions"]
    overrides = load_overrides()
    report: dict[str, Any] = {
        "started_utc": datetime.now(UTC).isoformat(),
        "dataset": dataset,
        "quick": quick,
        "concurrency": concurrency,
        "duration_s": duration,
        "warmup_s": warmup,
    }

    # ---- Step A -----------------------------------------------------------------------
    p_rule = rule_pool_size(cfg, host_env)
    p = pool_size(cfg, host_env, overrides)
    report["step_a"] = {
        "rule": cfg["pgbouncer"]["pool_size_rule"],
        "postgres_physical_cores": int(host_env["POSTGRES_PHYSICAL_CORES"]),
        "effective_spindles": cfg["pgbouncer"]["effective_spindles"],
        "rule_pool_size": p_rule,
        "pool_size_under_test": p,
        "overrides": overrides,
    }
    lint_results = lint_experiment(cfg, REPO_ROOT)
    (out / "compatibility_lint.md").write_text(report_markdown(lint_results), encoding="utf-8")
    report["step_a"]["lint_passed"] = all(r.ok for r in lint_results)
    with db.pgb_admin(ep) as admin:
        report["pgbouncer_config_start"] = {
            r["key"]: r["value"] for r in db.admin_rows(admin, "SHOW CONFIG")
        }
        report["pgbouncer_version"] = db.admin_rows(admin, "SHOW VERSION")
    live_pool = int(report["pgbouncer_config_start"].get("default_pool_size", -1))
    if live_pool != p:
        raise RuntimeError(
            f"PgBouncer runs default_pool_size={live_pool}, expected {p}: "
            "run `make render` and reload PgBouncer first"
        )
    log(
        f"Step A: pool size rule -> {p_rule}, under test {p}; lint "
        f"{'OK' if report['step_a']['lint_passed'] else 'FAILED'}"
    )

    log_offset = _log_offset(ep.pgbouncer_log)
    rng = np.random.default_rng(cfg["seeds"]["master"] + 1)
    seq = 0
    summaries: list[dict[str, Any]] = []

    def one(profile: str, c: int, label: str, pool: int) -> dict[str, Any]:
        nonlocal seq
        seq += 1
        req = RunRequest(
            run_dir=out / "runs" / f"{seq:03d}_{label}_{profile}_c{c}_pool{pool}",
            profile=profile,
            concurrency=c,
            path="pgbouncer",
            seed=int(rng.integers(1, 2**31 - 1)),
            dataset=dataset,
            warmup_s=warmup,
            measure_s=duration,
            do_reset=bool(cfg["profiles"][profile]["writes"]),
            keep_logs=False,
            label=f"calibration:{label}",
        )
        log(f"  {label}: {profile} c={c} pool={pool}")
        res = execute(cfg, ep, req)
        res.update({"profile": profile, "concurrency": c, "pool": pool, "check": label})
        summaries.append(res)
        return res

    # ---- Step B1 + B3: every profile error-free, no limit failures ----------------------
    log("Check 1: every profile through PgBouncer")
    b1 = []
    for profile in cfg["design"]["profiles"]:
        for c in concurrency:
            r = one(profile, c, "check1", p)
            b1.append(
                {
                    k: r[k]
                    for k in (
                        "profile",
                        "concurrency",
                        "tps",
                        "p95_ms",
                        "failed_tx",
                        "aborted_clients",
                        "pgbench_rc",
                        "sv_total_max",
                        "cl_waiting_max",
                        "limit_errors",
                    )
                }
            )
    report["check1_error_free"] = {
        "runs": b1,
        "passed": all(
            r["failed_tx"] == 0 and r["aborted_clients"] == 0 and r["pgbench_rc"] == 0 for r in b1
        ),
    }

    # ---- Step B2: backend rotation --------------------------------------------------------
    log("Check 2: pg_backend_pid() rotation")
    report["check2_rotation"] = check_rotation(
        cfg, ep, dataset, cal["rotation_transactions"], min(concurrency)
    )

    # ---- Step B4: pool size sanity ----------------------------------------------------------
    log("Check 4: pool size P vs P/2 and 2P")
    sizes = {f: max(1, int(round(p * f))) for f in cal["pool_size_factors"]}
    cells = [
        (prof, c, f)
        for prof in cal["pool_size_profiles"]
        for c in concurrency
        for f in cal["pool_size_factors"]
        for _ in range(reps)
    ]
    order = [cells[i] for i in rng.permutation(len(cells))]
    b4_runs = []
    try:
        for prof, c, f in order:
            _set_pool(ep, dbname, sizes[f])
            r = one(prof, c, "check4", sizes[f])
            b4_runs.append(
                {
                    "profile": prof,
                    "concurrency": c,
                    "factor": f,
                    "pool": sizes[f],
                    "tps": r["tps"],
                    "p95_ms": r["p95_ms"],
                    "failed_tx": r["failed_tx"],
                    "aborted_clients": r["aborted_clients"],
                }
            )
    finally:
        live = _restore_pool(ep, dbname)
    report["pgbouncer_config_end"] = live
    verdicts = []
    for prof in cal["pool_size_profiles"]:
        for c in concurrency:
            med = {}
            for f in cal["pool_size_factors"]:
                sel = [
                    x
                    for x in b4_runs
                    if x["profile"] == prof and x["concurrency"] == c and x["factor"] == f
                ]
                med[f] = {
                    "tps": median(x["tps"] for x in sel),
                    "p95_ms": median(x["p95_ms"] for x in sel),
                }
            best_tps = max(m["tps"] for m in med.values())
            best_p95 = min(m["p95_ms"] for m in med.values())
            ok_tps = med[1]["tps"] >= cal["tps_min_ratio"] * best_tps
            ok_p95 = med[1]["p95_ms"] <= cal["p95_max_ratio"] * best_p95
            verdicts.append(
                {
                    "profile": prof,
                    "concurrency": c,
                    "medians": med,
                    "best_tps": best_tps,
                    "best_p95_ms": best_p95,
                    "tps_ratio": med[1]["tps"] / best_tps if best_tps else None,
                    "p95_ratio": med[1]["p95_ms"] / best_p95 if best_p95 else None,
                    "passed": ok_tps and ok_p95,
                }
            )
    report["check4_pool_size"] = {
        "sizes": sizes,
        "runs": b4_runs,
        "verdicts": verdicts,
        "passed": all(v["passed"] for v in verdicts),
    }

    # ---- Step B3 ------------------------------------------------------------------------
    limit_hits = sum(sum((s.get("limit_errors") or {}).values()) for s in summaries)
    log_lines = _log_matches(ep.pgbouncer_log, log_offset)
    report["check3_limits"] = {
        "pgbench_limit_errors": limit_hits,
        "pgbouncer_log_matches": log_lines,
        "pgbouncer_log_checked": log_lines is not None,
        "passed": limit_hits == 0 and not log_lines,
    }

    report["live_pool_restored"] = int(live.get("default_pool_size", -1)) == p
    report["passed"] = (
        all(
            report[k]["passed"]
            for k in ("check1_error_free", "check2_rotation", "check3_limits", "check4_pool_size")
        )
        and report["step_a"]["lint_passed"]
        and report["live_pool_restored"]
    )
    report["finished_utc"] = datetime.now(UTC).isoformat()
    (out / "REPORT.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    (out / "REPORT.md").write_text(report_md(cfg, report), encoding="utf-8")
    log(f"calibration {'PASSED' if report['passed'] else 'FAILED'}: {out / 'REPORT.md'}")
    return out


def _fmt(x: Any, nd: int = 1) -> str:
    if x is None:
        return "-"
    if isinstance(x, float):
        return f"{x:,.{nd}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return str(x)


def _basis(overrides: dict[str, Any], param: str, default: str) -> str:
    """Basis column of Tabel 3.2: the rule/default, or the recorded single adjustment."""
    ov = (overrides or {}).get(param)
    if not ov:
        return default
    return f"Disesuaikan satu kali (aturan awal: {default}). Alasan: {ov['reason']}"


def report_md(cfg: dict[str, Any], r: dict[str, Any]) -> str:
    """Indonesian report, written so it can be turned into the RM1 answer (Bab 4.4 v5)."""
    a = r["step_a"]
    pgb = cfg["pgbouncer"]
    ok = lambda b: "lolos" if b else "TIDAK LOLOS"  # noqa: E731
    p = a["pool_size_under_test"]
    lines = [
        "# Laporan penetapan konfigurasi PgBouncer (RM1)",
        "",
        f"Dataset: `{r['dataset']}`, konkurensi {r['concurrency']}, durasi {r['duration_s']} detik "
        f"per pengujian (pemanasan {r['warmup_s']} detik). Data pada laporan ini adalah data "
        "pengamatan awal dan **tidak dipakai sebagai data pengujian kinerja**.",
        "",
        f"Hasil keseluruhan: **{ok(r['passed'])}**.",
        "",
        "## Langkah 1. Kajian dokumentasi dan literatur",
        "",
        f"- Aturan ukuran pool: `{a['rule']}` (PostgreSQL Wiki, 2014).",
        f"- Jumlah inti fisik pada cpuset PostgreSQL: {a['postgres_physical_cores']}; "
        f"spindle efektif: {a['effective_spindles']}.",
        f"- Nilai hasil aturan: **{a['rule_pool_size']}**. Nilai yang diuji: **{p}**.",
        "- Penyesuaian yang tercatat: "
        + (
            "; ".join(
                f"{k} = {v['value']} ({v['reason']})"
                for k, v in (a["overrides"] or {}).items()
                if not k.startswith("_")
            )
            or "tidak ada"
        )
        + ".",
        f"- Pemeriksaan kompatibilitas skrip terhadap Tabel 2.2: **{ok(a['lint_passed'])}** "
        "(rincian di `compatibility_lint.md`).",
        "",
        "## Langkah 2. Pengamatan awal",
        "",
        f"### Pemeriksaan 1: seluruh profil berjalan tanpa error ({ok(r['check1_error_free']['passed'])})",
        "",
        "| Profil | Konkurensi | TPS | Latensi p95 (ms) | Transaksi gagal | Klien aborted |",
        "|---|---|---|---|---|---|",
    ]
    for x in r["check1_error_free"]["runs"]:
        lines.append(
            f"| {x['profile']} | {x['concurrency']} | {_fmt(x['tps'])} | "
            f"{_fmt(x['p95_ms'], 2)} | {x['failed_tx']} | {x['aborted_clients']} |"
        )
    rot = r["check2_rotation"]
    lines += [
        "",
        f"### Pemeriksaan 2: rotasi koneksi backend ({ok(rot['passed'])})",
        "",
        f"Satu klien menjalankan {rot['pgbouncer']['transactions']} transaksi terpisah "
        f"`SELECT pg_backend_pid()` sementara beban latar ({rot['background']}) berjalan.",
        "",
        "| Jalur | Transaksi | PID backend berbeda |",
        "|---|---|---|",
        f"| PgBouncer | {rot['pgbouncer']['transactions']} | {rot['pgbouncer']['distinct_pids']} |",
        f"| Langsung (kontrol) | {rot['direct']['transactions']} | {rot['direct']['distinct_pids']} |",
        "",
        f"### Pemeriksaan 3: tidak ada kegagalan karena batas koneksi atau antrean "
        f"({ok(r['check3_limits']['passed'])})",
        "",
        f"- Error batas koneksi/antrean di keluaran pgbench: {r['check3_limits']['pgbench_limit_errors']}",
        f"- Baris log PgBouncer yang cocok: "
        f"{len(r['check3_limits']['pgbouncer_log_matches'] or []) if r['check3_limits']['pgbouncer_log_checked'] else 'log tidak terbaca'}",
        "",
        f"### Pemeriksaan 4: ukuran pool dibanding setengah dan dua kali nilainya "
        f"({ok(r['check4_pool_size']['passed'])})",
        "",
        f"Ukuran yang dibandingkan: {r['check4_pool_size']['sizes']}. Kriteria (D3): median TPS "
        f"pada P minimal {int(cfg['calibration']['tps_min_ratio'] * 100)}% dari median terbaik "
        f"dan median p95 pada P maksimal {int(cfg['calibration']['p95_max_ratio'] * 100)}% dari "
        "median terbaik.",
        "",
        "| Profil | Konkurensi | TPS P/2 | TPS P | TPS 2P | p95 P/2 | p95 P | p95 2P | Hasil |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for v in r["check4_pool_size"]["verdicts"]:
        m = v["medians"]
        fs = sorted(m)
        lines.append(
            f"| {v['profile']} | {v['concurrency']} | "
            + " | ".join(_fmt(m[f]["tps"]) for f in fs)
            + " | "
            + " | ".join(_fmt(m[f]["p95_ms"], 2) for f in fs)
            + f" | {ok(v['passed'])} |"
        )
    lines += [
        "",
        "## Langkah 3. Konfigurasi akhir (isi Tabel 3.2)",
        "",
        "| Parameter | Nilai | Dasar penetapan |",
        "|---|---|---|",
        f"| pool_mode | {pgb['pool_mode']} | Subbab 2.5; seluruh profil lolos pemeriksaan Tabel 2.2 |",
        f"| default_pool_size | {p} | "
        + _basis(
            a["overrides"],
            "default_pool_size",
            f"(2 × {a['postgres_physical_cores']} inti fisik) + "
            f"{a['effective_spindles']} spindle efektif; diperiksa pada pemeriksaan 4",
        )
        + " |",
        f"| max_db_connections | {p} | Sama dengan default_pool_size |",
        f"| reserve_pool_size | {pgb['reserve_pool_size']} | Tidak ada koneksi tambahan selama pengujian |",
        f"| max_client_conn | {r['pgbouncer_config_end'].get('max_client_conn')} | "
        "Lebih besar dari konkurensi tertinggi |",
        f"| query_wait_timeout | {r['pgbouncer_config_end'].get('query_wait_timeout')} | "
        + _basis(a["overrides"], "query_wait_timeout", "Nilai bawaan")
        + " |",
        f"| max_prepared_statements | {r['pgbouncer_config_end'].get('max_prepared_statements')} | "
        "Nilai bawaan sejak 1.24.0 |",
        "",
        f"Versi PgBouncer: {r['pgbouncer_version']}.",
        "",
        "Bila ada pemeriksaan yang tidak lolos: sesuaikan nilai yang bersangkutan **satu kali** "
        'dengan `make adjust PARAM=... VALUE=... REASON="..."`, jalankan `make render reload`, '
        "lalu ulangi `make calibrate`. Bila semua lolos: `make lock`.",
    ]
    return "\n".join(lines) + "\n"


def adjust(
    param: str, value: int, reason: str, calibration_dir: str | None, max_adjustments: int
) -> dict[str, Any]:
    """Record the single allowed adjustment of one Tabel 3.2 value (Subbab 3.3)."""
    if param not in ADJUSTABLE:
        raise ValueError(f"only {ADJUSTABLE} can be adjusted")
    if not reason.strip():
        raise ValueError("a written reason is required")
    if lock.read_lock() is not None:
        raise RuntimeError("the configuration is already locked")
    data = load_overrides()
    history = data.get("_history", [])
    used = sum(1 for h in history if h["param"] == param)
    if used >= max_adjustments:
        raise RuntimeError(
            f"{param} was already adjusted {used} time(s); the proposal allows "
            f"{max_adjustments}. Discuss with the supervisor before going further."
        )
    entry = {
        "value": value,
        "reason": reason,
        "decided_utc": datetime.now(UTC).isoformat(),
        "after_calibration": calibration_dir,
    }
    data[param] = entry
    history.append({"param": param, **entry})
    data["_history"] = history
    OVERRIDES.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return entry


def latest_report(passed_only: bool = True) -> Path | None:
    base = results_dir() / "calibration"
    if not base.exists():
        return None
    for d in sorted(base.iterdir(), reverse=True):
        if d.name.endswith("-SMOKE"):
            continue
        rep = d / "REPORT.json"
        if rep.exists() and (not passed_only or json.loads(rep.read_text())["passed"]):
            return d
    return None


def check4_exception(report_dir: Path, reason: str) -> dict[str, Any]:
    """DEV-024: accept a calibration whose only failure is check 4, with a written reason.

    Checks 1-3, the lint and the pool restore must still pass; only the D3 pool-size
    criterion may fail. The failing cells are copied into LOCK.json with the reason.
    """
    if not reason.strip():
        raise ValueError("a written reason is required")
    if report_dir.name.endswith("-SMOKE"):
        raise ValueError("a smoke calibration cannot be accepted")
    r = json.loads((report_dir / "REPORT.json").read_text(encoding="utf-8"))
    if r.get("quick"):
        raise ValueError("a quick calibration cannot be accepted")
    if r["passed"]:
        raise ValueError("this calibration passed; no exception is needed")
    failed = [
        k for k in ("check1_error_free", "check2_rotation", "check3_limits") if not r[k]["passed"]
    ]
    if failed or not r["step_a"]["lint_passed"] or not r["live_pool_restored"]:
        raise ValueError(f"only check 4 may fail; also failed: {failed or 'lint or pool restore'}")
    return {
        "report": str(report_dir),
        "pool_size": r["step_a"]["pool_size_under_test"],
        "failed_check4_cells": [
            {k: v[k] for k in ("profile", "concurrency", "tps_ratio", "p95_ratio")}
            for v in r["check4_pool_size"]["verdicts"]
            if not v["passed"]
        ],
        "reason": reason,
        "decided_utc": datetime.now(UTC).isoformat(),
    }
