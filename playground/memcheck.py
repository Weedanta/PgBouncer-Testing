"""Memory check under the heaviest load (DEV-019).

The WSL2 VM is capped at 10 GB so that Windows keeps enough memory (DEV-019). The
highest level, c = max(design.concurrency), on the direct path opens one PostgreSQL
backend per client; smoke stops at c = 50 and calibration runs through PgBouncer, so
this is the first time that load meets the container memory limits. If a limit is
too tight the kernel OOM-kills a backend, PostgreSQL restarts every session, and a
campaign run becomes an incident. This check finds that out before calibration.

It runs the configured profile (R3 by default) on each configured path at the highest
level with short runs, through the same `execute()` the campaign uses, then compares
the peak memory of each container with its limit from config/host.env. The output is
evidence for Subbab 4.x (validasi lingkungan uji), never performance data.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from playground.config import results_dir
from playground.runner import RunRequest, execute, log
from playground.settings import Endpoints

CONTAINERS = ("postgres", "pgbouncer", "client")
LIMIT_KEYS = {
    "postgres": "MEM_POSTGRES_MB",
    "pgbouncer": "MEM_PGBOUNCER_MB",
    "client": "MEM_CLIENT_MB",
}


def container_max(df: pd.DataFrame | None, series: str, container: str) -> float | None:
    """Peak of one per-container series over the run window (bytes)."""
    if df is None or df.empty:
        return None
    sel = df[
        (df["series"] == series) & df["labels"].fillna("").str.contains(f'"name": "{container}"')
    ]
    vals = sel["value"].dropna()
    return float(vals.max()) if not vals.empty else None


def verdict(
    working_set_mb: float | None,
    rss_mb: float | None,
    shared_mb: float,
    limit_mb: float,
    headroom: float,
) -> tuple[str, str]:
    """Classify one container.

    working set = memory the kernel counts against the limit minus inactive page cache;
    it still holds active page cache, so it over-states what cannot be reclaimed.
    rss + shared_buffers is the part that can never be reclaimed (PostgreSQL only).
    """
    if working_set_mb is None:
        return "TIDAK ADA DATA", "seri memori Prometheus tidak tersedia untuk run ini"
    cap = headroom * limit_mb
    if working_set_mb <= cap:
        return "AMAN", f"puncak {working_set_mb:.0f} MB <= {cap:.0f} MB ({headroom:.0%} batas)"
    if rss_mb is not None and rss_mb + shared_mb <= cap:
        return (
            "AMAN",
            f"working set {working_set_mb:.0f} MB melewati {cap:.0f} MB, tetapi RSS + "
            f"shared_buffers {rss_mb + shared_mb:.0f} MB di bawahnya; sisanya page cache "
            "yang bisa dilepas kernel",
        )
    return (
        "MEPET",
        f"puncak {working_set_mb:.0f} MB > {cap:.0f} MB ({headroom:.0%} dari {limit_mb:.0f} MB)",
    )


def _read_prom(run_dir: Path) -> pd.DataFrame | None:
    p = run_dir / "prometheus.parquet"
    return pd.read_parquet(p) if p.exists() else None


def run_memcheck(
    cfg: dict[str, Any], ep: Endpoints, host_env: dict[str, str], dataset: str = "campaign"
) -> Path:
    mc = cfg["memcheck"]
    c = max(cfg["design"]["concurrency"])
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    label = "SMOKE-" if dataset == "smoke" else ""
    out = results_dir() / "memcheck" / f"{label}{stamp}"
    out.mkdir(parents=True, exist_ok=False)
    rng = np.random.default_rng(cfg["seeds"]["master"] + 2)
    mem_pg = float(host_env.get("MEM_POSTGRES_MB", "0") or 0)
    shared_mb = mem_pg * float(cfg["postgres"]["shared_buffers_fraction"])
    headroom = float(mc["headroom_fraction"])

    rows: list[dict[str, Any]] = []
    for i, path in enumerate(mc["paths"], start=1):
        rd = out / "runs" / f"{i:02d}_{mc['profile']}_c{c}_{path}"
        log(f"memcheck: {mc['profile']} c={c} path={path}")
        req = RunRequest(
            run_dir=rd,
            profile=mc["profile"],
            concurrency=c,
            path=path,
            seed=int(rng.integers(1, 2**31 - 1)),
            dataset=dataset,
            warmup_s=int(mc["warmup_s"]),
            measure_s=int(mc["measure_s"]),
            do_reset=bool(cfg["profiles"][mc["profile"]]["writes"]),
            keep_logs=False,
            label="memcheck",
        )
        res = execute(cfg, ep, req)
        prom_df = _read_prom(rd)
        for name in CONTAINERS:
            ws = container_max(prom_df, "mem_bytes_by_container", f"pgb-{name}")
            rss = container_max(prom_df, "mem_rss_bytes_by_container", f"pgb-{name}")
            ws_mb = ws / 2**20 if ws is not None else None
            rss_mb = rss / 2**20 if rss is not None else None
            limit = float(host_env.get(LIMIT_KEYS[name], "0") or 0)
            v, why = verdict(
                ws_mb, rss_mb, shared_mb if name == "postgres" else 0.0, limit, headroom
            )
            rows.append(
                {
                    "path": path,
                    "container": name,
                    "limit_mb": limit,
                    "working_set_peak_mb": ws_mb,
                    "rss_peak_mb": rss_mb,
                    "verdict": v,
                    "reason": why,
                }
            )
        rows.append(
            {
                "path": path,
                "container": "run",
                "limit_mb": None,
                "working_set_peak_mb": None,
                "rss_peak_mb": None,
                "verdict": "",
                "reason": json.dumps(
                    {
                        k: res.get(k)
                        for k in (
                            "tps",
                            "p95_ms",
                            "failed_tx",
                            "aborted_clients",
                            "pg_backends_max",
                            "flag_container_restarted",
                            "flag_client_cpu_saturated",
                        )
                    },
                    default=str,
                ),
                "_res": res,
            }
        )

    report = _report(cfg, rows, c, shared_mb, headroom, host_env)
    (out / "REPORT.md").write_text(report, encoding="utf-8")
    pd.DataFrame([{k: v for k, v in r.items() if k != "_res"} for r in rows]).to_csv(
        out / "memcheck.csv", index=False
    )
    return out


def overall(rows: list[dict[str, Any]], c: int) -> tuple[bool, list[str]]:
    """PASS only if every container is AMAN, nothing restarted or aborted, and the direct
    path held one backend per client."""
    problems: list[str] = []
    for r in rows:
        if r["container"] != "run" and r["verdict"] != "AMAN":
            problems.append(f"{r['path']}/{r['container']}: {r['verdict']} ({r['reason']})")
        if r["container"] == "run":
            res = r.get("_res", {})
            if res.get("flag_container_restarted"):
                problems.append(f"{r['path']}: PostgreSQL atau PgBouncer restart (kemungkinan OOM)")
            if (res.get("aborted_clients") or 0) > 0:
                problems.append(f"{r['path']}: {res.get('aborted_clients')} klien pgbench aborted")
            if r["path"] == "direct":
                b = res.get("pg_backends_max")
                if b is None or b < c:
                    problems.append(
                        f"direct: backend maksimum {b} < {c}; jalur langsung tidak menahan "
                        "satu backend per klien"
                    )
    return (not problems), problems


def _fmt(x: Any) -> str:
    return "-" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.0f}"


def _report(
    cfg: dict[str, Any],
    rows: list[dict[str, Any]],
    c: int,
    shared_mb: float,
    headroom: float,
    host_env: dict[str, str],
) -> str:
    ok, problems = overall(rows, c)
    mc = cfg["memcheck"]
    lines = [
        "# Uji memori pada beban tertinggi (DEV-019)",
        "",
        f"Profil {mc['profile']}, c = {c}, jalur {', '.join(mc['paths'])}, pemanasan "
        f"{mc['warmup_s']} detik, pengukuran {mc['measure_s']} detik. Data ini bukan data "
        "kinerja; hanya bukti bahwa batas memori kontainer cukup (validasi lingkungan uji).",
        "",
        f"Batas memori dari `config/host.env`: PostgreSQL {host_env.get('MEM_POSTGRES_MB')} MB "
        f"(shared_buffers sekitar {shared_mb:.0f} MB), PgBouncer "
        f"{host_env.get('MEM_PGBOUNCER_MB')} MB, klien {host_env.get('MEM_CLIENT_MB')} MB. "
        f"Kriteria aman: puncak di bawah {headroom:.0%} batas.",
        "",
        f"## Kesimpulan: {'LOLOS' if ok else 'PERLU TINDAKAN'}",
        "",
    ]
    if ok:
        lines.append("Semua kontainer aman. Lanjutkan ke `calibrate`.")
    else:
        lines += [f"- {p}" for p in problems]
        lines += [
            "",
            "Tindakan yang disarankan: naikkan `memory` di `.wslconfig` menjadi 11GB, jalankan "
            "`wsl --shutdown`, lalu `make probe render up` dan ulangi `make memcheck`. "
            "Bila tetap mepet, diskusikan dengan pembimbing sebelum mengubah rancangan.",
        ]
    lines += [
        "",
        "## Rincian per kontainer",
        "",
        "| Jalur | Kontainer | Batas (MB) | Puncak working set (MB) | Puncak RSS (MB) "
        "| Status | Keterangan |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        if r["container"] == "run":
            continue
        lines.append(
            f"| {r['path']} | {r['container']} | {_fmt(r['limit_mb'])} | "
            f"{_fmt(r['working_set_peak_mb'])} | {_fmt(r['rss_peak_mb'])} | {r['verdict']} | "
            f"{r['reason']} |"
        )
    lines += [
        "",
        "## Ringkasan run",
        "",
        "| Jalur | TPS | p95 (ms) | Backend maks | Gagal | Aborted | Restart |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        if r["container"] != "run":
            continue
        res = r.get("_res", {})
        lines.append(
            f"| {r['path']} | {_fmt(res.get('tps'))} | {res.get('p95_ms') or '-'} | "
            f"{_fmt(res.get('pg_backends_max'))} | {res.get('failed_tx')} | "
            f"{res.get('aborted_clients')} | {bool(res.get('flag_container_restarted'))} |"
        )
    lines += [
        "",
        "Catatan: working set adalah memori yang dihitung kernel terhadap batas kontainer "
        "dikurangi page cache tidak aktif; nilainya masih memuat page cache aktif, jadi "
        "cenderung lebih besar daripada memori yang benar-benar tidak bisa dilepas. Pada "
        "PostgreSQL, RSS + shared_buffers adalah bagian yang tidak bisa dilepas.",
        "",
    ]
    return "\n".join(lines)
