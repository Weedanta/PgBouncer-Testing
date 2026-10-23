"""End-to-end analysis on a SYNTHETIC campaign directory (never under results/)."""

import json

import numpy as np
import pandas as pd

from analysis.export import export
from analysis.pipeline import run_analysis
from playground.config import load_experiment
from playground.runner import RUN_FIELDS


def make_synthetic_campaign(tmp_path, cfg):
    rng = np.random.default_rng(11)
    rows, seq = [], 0
    for rnd in range(1, cfg["design"]["rounds"] + 1):
        for profile in cfg["design"]["profiles"]:
            for c in cfg["design"]["concurrency"]:
                gain = (c - 300) / 2000  # synthetic: PgBouncer helps more at high c
                for path in ("direct", "pgbouncer"):
                    seq += 1
                    mult = 1 + gain if path == "pgbouncer" else 1.0
                    tpcb = profile in ("R3", "R4")
                    # synthetic: row-lock waits on pgbench_branches for R3/R4 above c = 100,
                    # fewer behind PgBouncer (it limits active transactions)
                    waits = (c / 100 - 1) * (30 if path == "direct" else 3) if tpcb else 0.2
                    row = {k: None for k in RUN_FIELDS}
                    row.update(
                        {
                            "seq": seq,
                            "run_id": f"{seq:03d}",
                            "round": rnd,
                            "profile": profile,
                            "concurrency": c,
                            "path": path,
                            "pair_id": f"r{rnd}-{profile}-c{c}",
                            "pgbench_seed": 1,
                            "tps": 1000 * mult * rng.normal(1, 0.01),
                            "p95_ms": 40 / mult * rng.normal(1, 0.01),
                            "p50_ms": 10.0,
                            "p99_ms": 90.0,
                            "lat_mean_ms": 15.0,
                            "failed_tx": 0,
                            "aborted_clients": 0,
                            "flag_failed_tx": False,
                            "smoke": False,
                            "lock_waiting_mean": waits,
                            "lock_waiting_max": 2 * waits,
                            "deadlocks": 0,
                            # synthetic memory: 2 MB private memory per PostgreSQL backend,
                            # 2 kB per PgBouncer client, pool of 6 backends
                            "pg_backends_mean": float(c if path == "direct" else min(c, 6)),
                            "postgres_rss_mb_mean": 60
                            + 2.0 * (c if path == "direct" else min(c, 6))
                            + rng.normal(0, 0.5),
                            "pgbouncer_rss_mb_mean": 4 + c * 2 / 1024 + rng.normal(0, 0.01)
                            if path == "pgbouncer"
                            else 4.0,
                        }
                    )
                    rows.append(row)
                    if rnd == 1:
                        port = "5432" if path == "direct" else "6432"
                        host = "postgres" if path == "direct" else "pgbouncer"
                        cmd = ["pgbench", "-h", host, "-p", port, "-c", str(c), "-j", str(c)]
                        if profile == "R5":
                            cmd += ["-f", "/work/workloads/r5/autosave.sql@35"]
                        else:
                            cmd += ["-b", "tpcb-like"]
                        if profile == "R4":
                            cmd.append("-C")
                        cmd.append("bench")
                        rd = tmp_path / "SYNTHETIC-campaign" / "runs" / row["run_id"]
                        rd.mkdir(parents=True)
                        (rd / "meta.json").write_text(json.dumps({"measure_cmd": cmd}))
    camp = tmp_path / "SYNTHETIC-campaign"
    camp.mkdir(exist_ok=True)
    pd.DataFrame(rows, columns=RUN_FIELDS).to_csv(camp / "runs.csv", index=False)
    (camp / "campaign.json").write_text(
        json.dumps({"campaign_id": "SYNTHETIC-campaign", "smoke": False, "experiment": cfg})
    )
    return camp


def test_full_analysis_on_synthetic_campaign(tmp_path):
    cfg = load_experiment()
    camp = make_synthetic_campaign(tmp_path, cfg)
    out = run_analysis(camp, cfg)
    hyp = pd.read_csv(out / "tables" / "05_hypotheses.csv")
    assert len(hyp) == 20
    aov = pd.read_csv(out / "tables" / "06_anova.csv")
    assert set(aov["profile"]) == set(cfg["design"]["profiles"])
    inter = aov[(aov["metric"] == "tps") & (aov["effect"] == "jalur x konkurensi")]
    assert (inter["p"] < 0.001).all()  # synthetic: the impact grows with concurrency
    delta = pd.read_csv(out / "tables" / "07_delta_by_level.csv")
    assert len(delta) == 5 * 5 * 5  # profiles x metrics x levels
    report = (out / "REPORT.md").read_text()
    assert "berubah tanda" in report  # Δ% TPS turns positive between c=100 and c=500
    # RM2 v5: memory per connection and capacity at an equal budget
    mem = pd.read_csv(out / "tables" / "13_memory_per_connection.csv")
    persistent = mem[mem["persistent"]]
    assert persistent["direct_mb_per_backend"].between(1.9, 2.1).all()
    assert (persistent["pgb_mb_per_client"] * 1024).between(1.5, 2.5).all()
    cap = pd.read_csv(out / "tables" / "14_capacity.csv")
    assert set(cap["profile"]) == {"R1", "R2", "R3", "R5"}  # R4 (-C) left out
    one_gb = cap[cap["budget_mb"] == 1024]
    assert one_gb["direct"].between(480, 540).all()
    assert (one_gb["pgbouncer"] > 50 * one_gb["direct"]).all()
    assert "Memori per koneksi dan kapasitas" in report
    assert (out / "figures" / "capacity_by_budget.png").exists()
    assert (out / "figures" / "memory_vs_concurrency.png").exists()
    assert (out / "figures" / "heatmap_delta_tps.png").exists()
    # proposal v4, Subbab 3.5.2: lock waits per profile, R3/R4 marked
    lk = pd.read_csv(out / "tables" / "11_lock_contention.csv")
    assert len(lk) == 5 * 5 * 2
    assert set(lk.loc[lk["updates_branches"], "profile"]) == {"R3", "R4"}
    assert "Perebutan lock" in report
    assert (out / "figures" / "support_lock_waiting.png").exists()
    # proposal v4, Bab 4: pgbench command, port and scripts per measurement unit
    cmds = pd.read_csv(out / "tables" / "12_commands.csv")
    assert len(cmds) == 5 * 5 * 2
    assert set(cmds.loc[cmds["path"] == "pgbouncer", "port"]) == {6432}
    assert cmds.loc[cmds["profile"] == "R5", "scripts"].str.contains("workloads/r5/").all()
    assert cmds.loc[cmds["profile"] == "R4", "connect_per_tx"].all()
    thesis = export(out)
    assert (thesis / "CAPTIONS.md").exists()
    assert "Perintah pgbench" in (thesis / "CAPTIONS.md").read_text(encoding="utf-8")
