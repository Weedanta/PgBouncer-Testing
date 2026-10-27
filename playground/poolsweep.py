"""Additional "pengamatan awal" step for the pool size (DEV-022, 27 Oct 2026).

Check 4 of the calibration failed on 27 Oct 2026: twice the rule's pool size gave far
higher TPS and lower p95. Before the single allowed adjustment of default_pool_size,
this step finds the value to adjust to with a rule fixed in advance:

- grid: the current pool size P doubled step by step (P, 2P, 4P, 8P, 16P);
- candidates: 2P, 4P, 8P, in that order; a candidate is accepted when it meets the
  check-4 criterion (D3) against its own half and double, for every profile and level
  of check 4: median TPS >= tps_min_ratio x best median TPS, and median p95 <=
  p95_max_ratio x best median p95;
- the smallest accepted candidate is the result (Schroeder et al., 2006: the lowest
  multi-programming level whose throughput loss stays within a threshold);
- sizes are measured stage by stage, so 8P and 16P are only run when needed; if no
  candidate is accepted, the step stops and the result is discussed with the supervisor.

This is calibration data (RM1), never performance data. The pool is changed live with
SET + KILL/RESUME and restored with RELOAD at the end, as in check 4.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np

from playground import db
from playground.calibrate import _fmt, _log_matches, _log_offset, _restore_pool, _set_pool
from playground.config import results_dir
from playground.render import load_overrides, pool_size
from playground.runner import RunRequest, execute, log
from playground.settings import Endpoints

GRID_FACTORS = (1, 2, 4, 8, 16)
CANDIDATE_FACTORS = (2, 4, 8)


def candidate_verdict(
    medians: dict[tuple[str, int, int], dict[str, float]],
    cells: list[tuple[str, int]],
    lower: int,
    cand: int,
    upper: int,
    tps_min_ratio: float,
    p95_max_ratio: float,
) -> dict[str, Any]:
    """D3 criterion for one candidate against its half (lower) and double (upper)."""
    rows = []
    for prof, c in cells:
        m = {s: medians[(prof, c, s)] for s in (lower, cand, upper)}
        best_tps = max(v["tps"] for v in m.values())
        best_p95 = min(v["p95_ms"] for v in m.values())
        ok_tps = m[cand]["tps"] >= tps_min_ratio * best_tps
        ok_p95 = m[cand]["p95_ms"] <= p95_max_ratio * best_p95
        rows.append(
            {
                "profile": prof,
                "concurrency": c,
                "medians": {str(s): v for s, v in m.items()},
                "tps_ratio": m[cand]["tps"] / best_tps if best_tps else None,
                "p95_ratio": m[cand]["p95_ms"] / best_p95 if best_p95 else None,
                "passed": ok_tps and ok_p95,
            }
        )
    return {
        "candidate": cand,
        "lower": lower,
        "upper": upper,
        "rows": rows,
        "passed": all(r["passed"] for r in rows),
    }


def run_pool_sweep(
    cfg: dict[str, Any], ep: Endpoints, host_env: dict[str, str], dataset: str = "campaign"
) -> Path:
    cal = cfg["calibration"]
    dbname = cfg["datasets"][dataset]["database"]
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = results_dir() / "poolsweep" / (f"{ts}-SMOKE" if dataset == "smoke" else ts)
    out.mkdir(parents=True, exist_ok=False)
    concurrency = cfg["smoke"]["concurrency"][-2:] if dataset == "smoke" else cal["concurrency"]
    profiles = cal["pool_size_profiles"]
    reps = cal["pool_size_repetitions"]
    overrides = load_overrides()
    if "default_pool_size" in overrides:
        raise RuntimeError(
            "default_pool_size was already adjusted; the sweep is only for choosing that "
            "single adjustment"
        )
    p = pool_size(cfg, host_env, overrides)
    grid = [p * f for f in GRID_FACTORS]
    cells = [(prof, c) for prof in profiles for c in concurrency]
    with db.pgb_admin(ep) as admin:
        config_start = {r["key"]: r["value"] for r in db.admin_rows(admin, "SHOW CONFIG")}
    report: dict[str, Any] = {
        "started_utc": datetime.now(UTC).isoformat(),
        "dataset": dataset,
        "rule_pool_size": p,
        "grid": grid,
        "candidates": [p * f for f in CANDIDATE_FACTORS],
        "profiles": profiles,
        "concurrency": concurrency,
        "repetitions": reps,
        "duration_s": cal["duration_s"],
        "warmup_s": cal["warmup_s"],
        "criterion": {"tps_min_ratio": cal["tps_min_ratio"], "p95_max_ratio": cal["p95_max_ratio"]},
        "pgbouncer_config_start": config_start,
        "overrides": overrides,
    }
    log_offset = _log_offset(ep.pgbouncer_log)
    rng = np.random.default_rng(cfg["seeds"]["master"] + 2)
    runs: list[dict[str, Any]] = []
    measured: set[int] = set()
    verdicts: list[dict[str, Any]] = []
    chosen: int | None = None
    seq = 0

    def measure(sizes: list[int]) -> None:
        nonlocal seq
        todo = [(prof, c, s) for s in sizes for prof, c in cells for _ in range(reps)]
        for i in rng.permutation(len(todo)):
            prof, c, s = todo[i]
            seq += 1
            _set_pool(ep, dbname, s)
            req = RunRequest(
                run_dir=out / "runs" / f"{seq:03d}_{prof}_c{c}_pool{s}",
                profile=prof,
                concurrency=c,
                path="pgbouncer",
                seed=int(rng.integers(1, 2**31 - 1)),
                dataset=dataset,
                warmup_s=cal["warmup_s"],
                measure_s=cal["duration_s"],
                do_reset=bool(cfg["profiles"][prof]["writes"]),
                keep_logs=False,
                label="calibration:poolsweep",
            )
            log(f"  poolsweep: {prof} c={c} pool={s}")
            r = execute(cfg, ep, req)
            runs.append(
                {
                    "profile": prof,
                    "concurrency": c,
                    "pool": s,
                    "tps": r["tps"],
                    "p95_ms": r["p95_ms"],
                    "failed_tx": r["failed_tx"],
                    "aborted_clients": r["aborted_clients"],
                }
            )
        measured.update(sizes)

    def medians() -> dict[tuple[str, int, int], dict[str, float]]:
        out_m = {}
        for prof, c in cells:
            for s in measured:
                sel = [
                    x for x in runs if (x["profile"], x["concurrency"], x["pool"]) == (prof, c, s)
                ]
                out_m[(prof, c, s)] = {
                    "tps": median(x["tps"] for x in sel),
                    "p95_ms": median(x["p95_ms"] for x in sel),
                }
        return out_m

    try:
        log(f"Pool sweep: grid {grid}, stage 1 measures {grid[:3]}")
        measure(grid[:3])
        for k, cand in enumerate(report["candidates"], start=1):
            upper = grid[k + 1]
            if upper not in measured:
                log(f"Pool sweep: candidate {cand} needs {upper}; measuring it")
                measure([upper])
            v = candidate_verdict(
                medians(),
                cells,
                grid[k - 1],
                cand,
                upper,
                cal["tps_min_ratio"],
                cal["p95_max_ratio"],
            )
            verdicts.append(v)
            log(f"Pool sweep: candidate {cand} {'ACCEPTED' if v['passed'] else 'rejected'}")
            if v["passed"]:
                chosen = cand
                break
    finally:
        live = _restore_pool(ep, dbname)
    report.update(
        {
            "runs": runs,
            "measured": sorted(measured),
            "medians": [
                {"profile": k[0], "concurrency": k[1], "pool": k[2], **v}
                for k, v in sorted(medians().items())
            ],
            "verdicts": verdicts,
            "chosen": chosen,
            "failures": sum(1 for x in runs if x["failed_tx"] or x["aborted_clients"]),
            "pgbouncer_log_matches": _log_matches(ep.pgbouncer_log, log_offset),
            "pgbouncer_config_end": live,
            "live_pool_restored": int(live.get("default_pool_size", -1)) == p,
            "finished_utc": datetime.now(UTC).isoformat(),
        }
    )
    (out / "REPORT.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    (out / "REPORT.md").write_text(report_md(report), encoding="utf-8")
    log(f"pool sweep: chosen = {chosen}; report {out / 'REPORT.md'}")
    return out


def report_md(r: dict[str, Any]) -> str:
    crit = r["criterion"]
    lines = [
        "# Pengamatan awal tambahan: ukuran pool (DEV-022)",
        "",
        "Data pada laporan ini adalah data penetapan konfigurasi (RM1) dan **tidak dipakai "
        "sebagai data pengujian kinerja**.",
        "",
        f"- Ukuran pool hasil aturan (P): **{r['rule_pool_size']}**. Deret yang disiapkan: "
        f"{r['grid']}; yang benar-benar diukur: {r['measured']}.",
        f"- Profil {', '.join(r['profiles'])}, konkurensi {r['concurrency']}, "
        f"{r['repetitions']} pengulangan, pemanasan {r['warmup_s']} detik, pengukuran "
        f"{r['duration_s']} detik, urutan diacak di setiap tahap.",
        f"- Kriteria (sama dengan pemeriksaan 4, D3): median TPS kandidat minimal "
        f"{int(crit['tps_min_ratio'] * 100)}% dari median terbaik di antara setengah, kandidat, "
        f"dan dua kali kandidat; median p95 kandidat maksimal {int(crit['p95_max_ratio'] * 100)}% "
        "dari median terbaik. Kandidat diperiksa dari yang terkecil; yang pertama lolos dipilih "
        "(Schroeder, et al., 2006).",
        f"- Run dengan transaksi gagal atau klien aborted: {r['failures']}. Baris log PgBouncer "
        f"terkait batas koneksi/antrean: {len(r['pgbouncer_log_matches'] or [])}.",
        "",
        "## Hasil: "
        + (
            f"ukuran pool terpilih **{r['chosen']}**"
            if r["chosen"]
            else "**tidak ada kandidat yang lolos**; hentikan dan bahas dengan pembimbing"
        ),
        "",
        "## Median per ukuran pool",
        "",
        "| Profil | Konkurensi | Pool | Median TPS | Median p95 (ms) |",
        "|---|---|---|---|---|",
    ]
    for m in r["medians"]:
        lines.append(
            f"| {m['profile']} | {m['concurrency']} | {m['pool']} | {_fmt(m['tps'])} | "
            f"{_fmt(m['p95_ms'], 2)} |"
        )
    lines += ["", "## Pemeriksaan kandidat", ""]
    for v in r["verdicts"]:
        lines += [
            f"### Kandidat {v['candidate']} (dibanding {v['lower']} dan {v['upper']}): "
            f"{'LOLOS' if v['passed'] else 'tidak lolos'}",
            "",
            "| Profil | Konkurensi | Rasio TPS | Rasio p95 | Hasil |",
            "|---|---|---|---|---|",
        ]
        for row in v["rows"]:
            lines.append(
                f"| {row['profile']} | {row['concurrency']} | {_fmt(row['tps_ratio'], 3)} | "
                f"{_fmt(row['p95_ratio'], 3)} | {'lolos' if row['passed'] else 'tidak lolos'} |"
            )
        lines.append("")
    if r["chosen"]:
        lines += [
            "## Langkah berikutnya",
            "",
            f'`make adjust PARAM=default_pool_size VALUE={r["chosen"]} REASON="..."`, lalu '
            "`make reload` dan kalibrasi ulang penuh.",
            "",
        ]
    return "\n".join(lines)
