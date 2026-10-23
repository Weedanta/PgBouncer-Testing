"""Analysis pipeline (Subbab 3.6, proposal v5): runs.csv -> tables, figures, REPORT.md.

Steps (numbering of the proposal):
 1. per-run summary and Delta% = (pgbouncer - direct) / direct x 100 per pair (descriptive)
 2. two-factor full factorial with replications on ln(value) per profile and metric
    (Jain, 1991); prerequisites: Shapiro-Wilk on residuals, Levene across the cells
 3. H1/H2: one-sided contrast pgbouncer vs direct at c = 500 and 1000 for every profile,
    pooled model error, Holm over the family of 20 comparisons
 4. does the impact change with concurrency: the path x concurrency interaction F test,
    with partial eta squared; Delta% with a 95% CI for every concurrency level
 5. memory and capacity (RM2 v5): private memory per connection from the slope of RSS
    against the number of connections, and the number of connections that fit in an
    equal memory budget on each path, next to the throughput measured at every level
 6. trend figures with supporting data (backends, cl_waiting, sv_active, CPU, memory)
Plus: lock waits and deadlocks per profile (R3/R4 row-lock contention on pgbench_branches,
Subbab 3.5.2), and the pgbench command, port and scripts of every measurement unit (Bab 4).
Everything is deterministic. Flagged runs are kept; a sensitivity table without them is
produced next to the primary one.
"""

from __future__ import annotations

import json
import math
import warnings
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from analysis import figures, report, stats
from analysis.load import (
    campaign_info,
    load_commands,
    load_prometheus,
    load_runs,
    load_samples,
    load_script_latency,
)

METRICS = {  # column -> (Indonesian label, H alternative for "improvement")
    "tps": ("Throughput (TPS)", "greater"),
    "lat_mean_ms": ("Latensi rata-rata (ms)", "less"),
    "p50_ms": ("Latensi p50 (ms)", "less"),
    "p95_ms": ("Latensi p95 (ms)", "less"),
    "p99_ms": ("Latensi p99 (ms)", "less"),
}
HYPOTHESES = {"H1": "tps", "H2": "p95_ms"}


def paired(runs: pd.DataFrame, metric: str) -> pd.DataFrame:
    wide = runs.pivot_table(
        index=["profile", "concurrency", "round", "pair_id"],
        columns="path",
        values=metric,
        aggfunc="first",
    ).reset_index()
    if "direct" not in wide or "pgbouncer" not in wide:
        return pd.DataFrame()
    wide = wide.dropna(subset=["direct", "pgbouncer"])
    wide["diff"] = wide["pgbouncer"] - wide["direct"]
    wide["delta_pct"] = wide["diff"] / wide["direct"] * 100.0
    wide["metric"] = metric
    return wide


def descriptives(runs: pd.DataFrame) -> pd.DataFrame:
    rows = []
    extra = ["failed_tx", "postgres_rss_mb_mean", "pgbouncer_rss_mb_mean"]
    for (profile, c, path), g in runs.groupby(["profile", "concurrency", "path"]):
        for metric in [*METRICS, *extra]:
            if metric not in g:
                continue
            d = stats.describe(pd.to_numeric(g[metric], errors="coerce").to_numpy())
            rows.append({"profile": profile, "concurrency": c, "path": path, "metric": metric, **d})
    return pd.DataFrame(rows)


def _log_frame(runs: pd.DataFrame, profile: str, metric: str) -> pd.DataFrame:
    g = runs[runs["profile"] == profile][["path", "concurrency", "round", metric]].copy()
    g[metric] = pd.to_numeric(g[metric], errors="coerce")
    g = g[g[metric] > 0]
    g["y"] = np.log(g[metric])
    return g


def factorial(runs: pd.DataFrame, cfg: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    """Fitted two-factor model per (profile, metric)."""
    out = {}
    for profile in cfg["design"]["profiles"]:
        for metric in METRICS:
            g = _log_frame(runs, profile, metric)
            if g.empty:
                continue
            fit = stats.factorial_anova(g)
            fit["data"] = g
            out[(profile, metric)] = fit
    return out


def anova_table(fits: dict[tuple[str, str], dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for (profile, metric), fit in fits.items():
        if not fit.get("ok"):
            continue
        for row in fit["rows"]:
            rows.append({"profile": profile, "metric": metric, "method": fit["method"], **row})
    return pd.DataFrame(rows)


def prerequisites(fits: dict[tuple[str, str], dict[str, Any]]) -> tuple[pd.DataFrame, pd.DataFrame]:
    sh, lv = [], []
    for (profile, metric), fit in fits.items():
        if not fit.get("ok"):
            continue
        sh.append({"profile": profile, "metric": metric, **stats.shapiro(fit["residuals"])})
        lv.append({"profile": profile, "metric": metric, **stats.levene(fit["cells"])})
    return pd.DataFrame(sh), pd.DataFrame(lv)


def deltas_by_level(fits: dict[tuple[str, str], dict[str, Any]]) -> pd.DataFrame:
    """Delta% (ratio of geometric means) with a 95% CI for every profile x c x metric."""
    rows = []
    for (profile, metric), fit in fits.items():
        if not fit.get("ok"):
            continue
        g = fit["data"]
        for c, gc in g.groupby("concurrency"):
            ypg = gc[gc["path"] == "pgbouncer"]["y"].to_numpy()
            ydi = gc[gc["path"] == "direct"]["y"].to_numpy()
            if not ypg.size or not ydi.size:
                continue
            res = stats.contrast(ypg, ydi, fit["mse"], fit["df_e"], "two-sided")
            rows.append(
                {
                    "profile": profile,
                    "concurrency": c,
                    "metric": metric,
                    "gmean_direct": float(np.exp(ydi.mean())),
                    "gmean_pgbouncer": float(np.exp(ypg.mean())),
                    **res,
                }
            )
    return pd.DataFrame(rows)


def hypotheses(runs: pd.DataFrame, cfg: dict[str, Any], high: list[int]) -> pd.DataFrame:
    an = cfg["analysis"]
    fits = factorial(runs, cfg)
    rows = []
    for hname, metric in HYPOTHESES.items():
        alt = METRICS[metric][1]
        for profile in cfg["design"]["profiles"]:
            fit = fits.get((profile, metric))
            if not fit or not fit.get("ok"):
                continue
            g = fit["data"]
            for c in high:
                gc = g[g["concurrency"] == c]
                ypg = gc[gc["path"] == "pgbouncer"]["y"].to_numpy()
                ydi = gc[gc["path"] == "direct"]["y"].to_numpy()
                if not ypg.size or not ydi.size:
                    continue
                res = stats.contrast(ypg, ydi, fit["mse"], fit["df_e"], alt)
                raw = runs[(runs["profile"] == profile) & (runs["concurrency"] == c)]
                rows.append(
                    {
                        "hypothesis": hname,
                        "metric": metric,
                        "profile": profile,
                        "concurrency": c,
                        "alternative": stats.ALTERNATIVE[alt],
                        "mean_direct": float(raw[raw["path"] == "direct"][metric].mean()),
                        "mean_pgbouncer": float(raw[raw["path"] == "pgbouncer"][metric].mean()),
                        "gmean_direct": float(np.exp(ydi.mean())),
                        "gmean_pgbouncer": float(np.exp(ypg.mean())),
                        **res,
                    }
                )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.rename(columns={"p": "p_raw"})
    df["p_holm"] = stats.holm(df["p_raw"].tolist())
    df["decision"] = np.where(df["p_holm"] < an["alpha"], "H0 ditolak", "H0 tidak ditolak")
    return df


def support(runs: pd.DataFrame, samples: pd.DataFrame, promdf: pd.DataFrame) -> pd.DataFrame:
    df = runs.merge(samples, on="run_id", how="left") if not samples.empty else runs.copy()
    if not promdf.empty:
        df = df.merge(promdf, on="run_id", how="left")
    keep = [
        c
        for c in df.columns
        if c.endswith("_mean")
        or c.endswith("_max")
        or c.startswith("cpu_cores_by_container")
        or c.startswith("mem_bytes_by_container")
        or c.startswith("mem_rss_bytes_by_container")
        or c
        in (
            "pgb_avg_wait_ms",
            "cache_hit_ratio",
            "deadlocks",
            "disk_read_bytes_rate",
            "disk_written_bytes_rate",
            "context_switches_rate",
        )
    ]
    keep = list(dict.fromkeys(c for c in keep if pd.api.types.is_numeric_dtype(df[c])))
    out = df.groupby(["profile", "concurrency", "path"])[keep].mean().reset_index()
    return out


def _persistent(cfg: dict[str, Any], profile: str) -> bool:
    return not cfg["profiles"][profile].get("connect_per_transaction", False)


def memory_fits(runs: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    """Private memory per connection (RM2 v5): slope of RSS against connections.

    Direct path: PostgreSQL RSS against the number of backends (sampler mean; falls back to
    the concurrency level). PgBouncer path: PgBouncer RSS against client connections (= c),
    and the PostgreSQL RSS that the pooled backends need.
    """
    need = {"postgres_rss_mb_mean", "pgbouncer_rss_mb_mean"}
    if runs.empty or not need <= set(runs.columns):
        return pd.DataFrame()
    rows = []
    for profile in cfg["design"]["profiles"]:
        g = runs[runs["profile"] == profile]
        if g.empty:
            continue
        d = g[g["path"] == "direct"]
        p = g[g["path"] == "pgbouncer"]
        x_d = d["pg_backends_mean"] if "pg_backends_mean" in d else d["concurrency"]
        x_d = x_d.fillna(d["concurrency"]) if x_d is not None else d["concurrency"]
        fd = stats.linear_fit(x_d.to_numpy(), d["postgres_rss_mb_mean"].to_numpy())
        fp = stats.linear_fit(p["concurrency"].to_numpy(), p["pgbouncer_rss_mb_mean"].to_numpy())
        pool = float(p["pg_backends_mean"].mean()) if "pg_backends_mean" in p else float("nan")
        rows.append(
            {
                "profile": profile,
                "persistent": _persistent(cfg, profile),
                "direct_mb_per_backend": fd["slope"],
                "direct_mb_per_backend_ci_low": fd["slope_ci_low"],
                "direct_mb_per_backend_ci_high": fd["slope_ci_high"],
                "direct_intercept_mb": fd["intercept"],
                "direct_r2": fd["r2"],
                "direct_n": fd["n"],
                "pgb_mb_per_client": fp["slope"],
                "pgb_mb_per_client_ci_low": fp["slope_ci_low"],
                "pgb_mb_per_client_ci_high": fp["slope_ci_high"],
                "pgb_base_mb": fp["intercept"],
                "pgb_r2": fp["r2"],
                "pgb_n": fp["n"],
                "pool_backends_mean": pool,
                "postgres_rss_on_pgb_path_mb": float(p["postgres_rss_mb_mean"].mean()),
            }
        )
    return pd.DataFrame(rows)


def capacity_table(fits: pd.DataFrame, cfg: dict[str, Any], levels: list[int]) -> pd.DataFrame:
    """Connections that fit in each memory budget on both paths (the supervisor's example)."""
    if fits.empty:
        return pd.DataFrame()
    mem = cfg["analysis"]["memory"]
    use = fits[fits["persistent"]] if mem.get("persistent_profiles_only", True) else fits
    top = max(levels)
    rows = []
    for _, f in use.iterrows():
        for budget in mem["budgets_mb"]:
            cap = stats.capacity(
                budget,
                f["direct_mb_per_backend"],
                f["pgb_mb_per_client"],
                f["pool_backends_mean"] if not math.isnan(f["pool_backends_mean"]) else 0.0,
                f["pgb_base_mb"] if not math.isnan(f["pgb_base_mb"]) else 0.0,
            )
            rows.append(
                {
                    "profile": f["profile"],
                    **cap,
                    "ratio": cap["pgbouncer"] / cap["direct"]
                    if cap["direct"] and not math.isnan(cap["direct"])
                    else float("nan"),
                    "direct_extrapolated": bool(cap["direct"] > top)
                    if not math.isnan(cap["direct"])
                    else None,
                    "pgbouncer_extrapolated": bool(cap["pgbouncer"] > top)
                    if not math.isnan(cap["pgbouncer"])
                    else None,
                }
            )
    return pd.DataFrame(rows)


def memory_by_level(runs: pd.DataFrame) -> pd.DataFrame:
    """Memory used for connections next to throughput, per level (the trade-off table)."""
    if runs.empty or "postgres_rss_mb_mean" not in runs:
        return pd.DataFrame()
    g = runs.copy()
    g["conn_memory_mb"] = g["postgres_rss_mb_mean"] + np.where(
        g["path"] == "pgbouncer", g["pgbouncer_rss_mb_mean"].fillna(0), 0.0
    )
    out = (
        g.groupby(["profile", "concurrency", "path"])
        .agg(
            conn_memory_mb=("conn_memory_mb", "mean"),
            postgres_rss_mb=("postgres_rss_mb_mean", "mean"),
            pgbouncer_rss_mb=("pgbouncer_rss_mb_mean", "mean"),
            tps_mean=("tps", "mean"),
            p95_ms_mean=("p95_ms", "mean"),
        )
        .reset_index()
    )
    out["tps_per_gb"] = out["tps_mean"] / (out["conn_memory_mb"] / 1024)
    return out


def updates_branches(cfg: dict[str, Any], profile: str) -> bool:
    """R3 and R4 (built-in tpcb-like) update pgbench_branches in every transaction."""
    return cfg["profiles"].get(profile, {}).get("builtin") == "tpcb-like"


def lock_contention(runs: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    """Sessions waiting for a lock and deadlocks per profile x c x path (Subbab 3.5.2)."""
    need = ("lock_waiting_mean", "lock_waiting_max", "deadlocks")
    if runs.empty or not all(c in runs for c in need):
        return pd.DataFrame()
    out = (
        runs.groupby(["profile", "concurrency", "path"])
        .agg(
            lock_waiting_median=("lock_waiting_mean", "median"),
            lock_waiting_peak=("lock_waiting_max", "max"),
            deadlocks_median=("deadlocks", "median"),
            deadlocks_total=("deadlocks", "sum"),
            failed_tx_total=("failed_tx", "sum"),
        )
        .reset_index()
    )
    out.insert(1, "updates_branches", out["profile"].map(lambda p: updates_branches(cfg, p)))
    return out


def flags_summary(runs: pd.DataFrame) -> pd.DataFrame:
    flag_cols = [c for c in runs.columns if c.startswith("flag_")]
    return (
        runs.groupby(["profile", "concurrency", "path"])[flag_cols]
        .apply(lambda g: g.fillna(False).astype(bool).sum())
        .reset_index()
    )


def run_analysis(campaign_dir: Path, cfg: dict[str, Any], out_dir: Path | None = None) -> Path:
    with warnings.catch_warnings():  # scipy warns on degenerate tiny samples (smoke data)
        warnings.simplefilter("ignore", RuntimeWarning)
        return _run_analysis(campaign_dir, cfg, out_dir)


def _run_analysis(campaign_dir: Path, cfg: dict[str, Any], out_dir: Path | None) -> Path:
    info = campaign_info(campaign_dir)
    smoke = bool(info.get("smoke"))
    out = out_dir or campaign_dir / "analysis"
    tables, figs = out / "tables", out / "figures"
    tables.mkdir(parents=True, exist_ok=True)
    figs.mkdir(parents=True, exist_ok=True)
    levels = sorted(
        info["experiment"]["smoke"]["concurrency"] if smoke else cfg["design"]["concurrency"]
    )
    high = levels[-2:] if smoke else cfg["design"]["high_concurrency"]

    runs = load_runs(campaign_dir)
    samples = load_samples(campaign_dir, runs)
    promdf = load_prometheus(campaign_dir, runs)
    scripts = load_script_latency(campaign_dir, runs)
    pairs = pd.concat([paired(runs, m) for m in METRICS], ignore_index=True)
    clean_runs = runs[~runs["pair_id"].isin(set(runs.loc[runs["flagged"], "pair_id"]))]

    fits = factorial(runs, cfg)
    result: dict[str, pd.DataFrame] = {
        "01_per_run": runs,
        "02_descriptives": descriptives(runs),
        "03_impact_pairs": pairs,
    }
    sh, lv = prerequisites(fits)
    result["04_shapiro"] = sh
    result["04_levene"] = lv
    result["05_hypotheses"] = hypotheses(runs, cfg, high)
    result["05_hypotheses_without_flagged"] = hypotheses(clean_runs, cfg, high)
    result["06_anova"] = anova_table(fits)
    result["07_delta_by_level"] = deltas_by_level(fits)
    result["08_support"] = support(runs, samples, promdf)
    result["09_flags"] = flags_summary(runs)
    result["11_lock_contention"] = lock_contention(runs, cfg)
    result["12_commands"] = load_commands(campaign_dir, runs)
    mfits = memory_fits(runs, cfg)
    result["13_memory_per_connection"] = mfits
    result["14_capacity"] = capacity_table(mfits, cfg, levels)
    result["15_memory_by_level"] = memory_by_level(runs)
    if not scripts.empty:
        result["10_r5_scripts"] = (
            scripts.groupby(["script", "concurrency", "path"])[
                ["p50_ms", "p95_ms", "p99_ms", "mean_ms", "n_ok", "n_failed"]
            ]
            .median()
            .reset_index()
        )
    for name, df in result.items():
        df.to_csv(tables / f"{name}.csv", index=False)
    with pd.ExcelWriter(out / "tables.xlsx") as xw:
        for name, df in result.items():
            df.to_excel(xw, sheet_name=name[:31], index=False)

    fig_list = figures.make_all(runs, pairs, result, levels, figs, cfg, smoke)
    text = report.report_md(info, cfg, result, levels, high, fig_list, smoke)
    (out / "REPORT.md").write_text(text, encoding="utf-8")
    (out / "PRACTITIONER_SUMMARY.md").write_text(
        report.practitioner_md(info, cfg, result, levels, smoke), encoding="utf-8"
    )
    (out / "analysis_meta.json").write_text(
        json.dumps(
            {
                "campaign": info["campaign_id"],
                "smoke": smoke,
                "n_runs": int(len(runs)),
                "n_pairs": int(pairs[pairs["metric"] == "tps"].shape[0]) if not pairs.empty else 0,
                "flagged_runs": int(runs["flagged"].sum()),
                "levels": levels,
                "high": high,
                "primary_test": cfg["analysis"]["primary_test"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return out
