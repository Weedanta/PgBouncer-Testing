"""Figures for Bab 5 (Hasil dan Pembahasan) (Indonesian labels, technical terms kept in English).

Colors are fixed across every figure: direct path = blue, PgBouncer path = orange.
Each figure is written as PNG (figure_dpi) and SVG.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

from analysis import stats  # noqa: E402

COLORS = {"direct": "#1f5a96", "pgbouncer": "#d1700f"}
LABELS = {"direct": "Jalur langsung (baseline)", "pgbouncer": "Jalur PgBouncer"}
X_LABEL = "Tingkat konkurensi (koneksi)"
METRIC_LABEL = {
    "tps": "Throughput (TPS)",
    "lat_mean_ms": "Latensi rata-rata (ms)",
    "p50_ms": "Latensi p50 (ms)",
    "p95_ms": "Latensi p95 (ms)",
    "p99_ms": "Latensi p99 (ms)",
}


def _comma(decimals: int = 0):
    def fmt(x: float, _pos: int) -> str:
        s = f"{x:,.{decimals}f}"
        return s.replace(",", "X").replace(".", ",").replace("X", ".")

    return FuncFormatter(fmt)


def _save(fig: plt.Figure, out: Path, name: str, dpi: int) -> str:
    fig.tight_layout()
    fig.savefig(out / f"{name}.png", dpi=dpi)
    fig.savefig(out / f"{name}.svg")
    plt.close(fig)
    return name


def _title(text: str, smoke: bool) -> str:
    return f"[SMOKE] {text}" if smoke else text


def _ci_series(
    runs: pd.DataFrame, profile: str, path: str, metric: str, levels: list[int]
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mean, lo, hi = [], [], []
    for c in levels:
        g = runs[
            (runs["profile"] == profile) & (runs["path"] == path) & (runs["concurrency"] == c)
        ][metric]
        d = stats.describe(g.to_numpy())
        mean.append(d.get("mean", np.nan))
        lo.append(d.get("ci_low", np.nan))
        hi.append(d.get("ci_high", np.nan))
    return np.array(mean), np.array(lo), np.array(hi)


def metric_vs_concurrency(
    runs: pd.DataFrame,
    profile: str,
    metric: str,
    levels: list[int],
    out: Path,
    dpi: int,
    smoke: bool,
) -> str:
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    x = np.arange(len(levels))
    for i, path in enumerate(("direct", "pgbouncer")):
        mean, lo, hi = _ci_series(runs, profile, path, metric, levels)
        off = (i - 0.5) * 0.06
        ax.errorbar(
            x + off,
            mean,
            yerr=[mean - lo, hi - mean],
            marker="o",
            capsize=4,
            color=COLORS[path],
            label=LABELS[path],
        )
    ax.set_xticks(x, [f"{c:,}".replace(",", ".") for c in levels])
    ax.set_xlabel(X_LABEL)
    ax.set_ylabel(METRIC_LABEL[metric])
    ax.yaxis.set_major_formatter(_comma(0 if metric == "tps" else 1))
    ax.set_title(_title(f"{profile}: {METRIC_LABEL[metric]} (rata-rata, SK 95%)", smoke))
    ax.grid(alpha=0.3)
    ax.legend()
    return _save(fig, out, f"{profile}_{metric}_vs_concurrency", dpi)


def latency_panel(
    runs: pd.DataFrame, profile: str, levels: list[int], out: Path, dpi: int, smoke: bool
) -> str:
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.8))
    x = np.arange(len(levels))
    for ax, metric in zip(axes, ("p50_ms", "p95_ms", "p99_ms"), strict=True):
        for path in ("direct", "pgbouncer"):
            mean, lo, hi = _ci_series(runs, profile, path, metric, levels)
            ax.errorbar(
                x,
                mean,
                yerr=[mean - lo, hi - mean],
                marker="o",
                capsize=3,
                color=COLORS[path],
                label=LABELS[path],
            )
        ax.set_xticks(x, [str(c) for c in levels])
        ax.set_xlabel(X_LABEL)
        ax.set_ylabel(METRIC_LABEL[metric])
        ax.yaxis.set_major_formatter(_comma(1))
        ax.grid(alpha=0.3)
    axes[0].legend(fontsize=8)
    fig.suptitle(_title(f"{profile}: persentil latensi", smoke))
    return _save(fig, out, f"{profile}_latency_percentiles", dpi)


def delta_heatmap(
    delta: pd.DataFrame,
    metric: str,
    levels: list[int],
    profiles: list[str],
    out: Path,
    dpi: int,
    smoke: bool,
) -> str | None:
    """Delta% from the factorial model (ratio of geometric means), profile x level."""
    if delta is None or delta.empty:
        return None
    sub = delta[delta["metric"] == metric]
    mat = np.full((len(profiles), len(levels)), np.nan)
    for i, p in enumerate(profiles):
        for j, c in enumerate(levels):
            v = sub[(sub["profile"] == p) & (sub["concurrency"] == c)]["delta_pct"]
            if not v.empty:
                mat[i, j] = float(v.iloc[0])
    # green = improvement on both metrics (TPS up, latency down)
    cmap = "RdYlGn" if metric == "tps" else "RdYlGn_r"
    lim = np.nanmax(np.abs(mat)) if np.isfinite(mat).any() else 1.0
    fig, ax = plt.subplots(figsize=(max(6.0, 1.5 + 1.3 * len(levels)), 1.6 + 0.6 * len(profiles)))
    im = ax.imshow(mat, cmap=cmap, vmin=-lim, vmax=lim, aspect="auto")
    for i in range(len(profiles)):
        for j in range(len(levels)):
            if np.isfinite(mat[i, j]):
                ax.text(
                    j,
                    i,
                    f"{mat[i, j]:+.1f}%".replace(".", ","),
                    ha="center",
                    va="center",
                    fontsize=9,
                )
    ax.set_xticks(range(len(levels)), [str(c) for c in levels])
    ax.set_yticks(range(len(profiles)), profiles)
    ax.set_xlabel(X_LABEL)
    ax.set_title(_title(f"Δ% {METRIC_LABEL[metric]}\n(PgBouncer dibanding jalur langsung)", smoke))
    fig.colorbar(im, ax=ax, label="Δ%")
    return _save(fig, out, f"heatmap_delta_{metric}", dpi)


def memory_vs_concurrency(
    mem: pd.DataFrame,
    levels: list[int],
    profiles: list[str],
    out: Path,
    dpi: int,
    smoke: bool,
) -> str | None:
    """Memory used for connections (RSS PostgreSQL, + PgBouncer on its path) per level."""
    if mem is None or mem.empty or mem["conn_memory_mb"].dropna().empty:
        return None
    return support_lines(
        mem,
        "conn_memory_mb",
        "Memori koneksi, RSS (MB)",
        "Memori privat untuk koneksi pada kedua jalur",
        "memory_vs_concurrency",
        levels,
        profiles,
        out,
        dpi,
        smoke,
    )


def capacity_bars(cap: pd.DataFrame, out: Path, dpi: int, smoke: bool) -> str | None:
    """Connections that fit in each memory budget, median over the persistent profiles."""
    if cap is None or cap.empty or cap[["direct", "pgbouncer"]].dropna().empty:
        return None
    med = cap.groupby("budget_mb")[["direct", "pgbouncer"]].median()
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    x = np.arange(len(med.index))
    for k, path in enumerate(("direct", "pgbouncer")):
        ax.bar(x + (k - 0.5) * 0.38, med[path], width=0.38, color=COLORS[path], label=LABELS[path])
    ax.set_xticks(x, [f"{int(b):,} MB".replace(",", ".") for b in med.index])
    ax.set_yscale("log")
    ax.set_xlabel("Anggaran memori untuk koneksi")
    ax.set_ylabel("Jumlah koneksi klien (skala log)")
    ax.set_title(_title("Kapasitas koneksi pada anggaran memori yang sama", smoke))
    ax.grid(alpha=0.3, axis="y")
    ax.legend()
    return _save(fig, out, "capacity_by_budget", dpi)


def support_lines(
    df: pd.DataFrame,
    column: str,
    ylabel: str,
    title: str,
    name: str,
    levels: list[int],
    profiles: list[str],
    out: Path,
    dpi: int,
    smoke: bool,
    paths: tuple[str, ...] = ("direct", "pgbouncer"),
) -> str | None:
    if column not in df or df[column].dropna().empty:
        return None
    fig, axes = plt.subplots(1, len(profiles), figsize=(3.0 * len(profiles), 3.2), sharey=True)
    axes = np.atleast_1d(axes)
    x = np.arange(len(levels))
    for ax, profile in zip(axes, profiles, strict=True):
        for path in paths:
            g = df[(df["profile"] == profile) & (df["path"] == path)].set_index("concurrency")
            y = [g[column].get(c, np.nan) if column in g else np.nan for c in levels]
            ax.plot(x, y, marker="o", color=COLORS[path], label=LABELS[path])
        ax.set_xticks(x, [str(c) for c in levels])
        ax.set_title(profile)
        ax.grid(alpha=0.3)
        ax.set_xlabel(X_LABEL, fontsize=8)
    axes[0].set_ylabel(ylabel)
    axes[0].legend(fontsize=7)
    fig.suptitle(_title(title, smoke))
    return _save(fig, out, name, dpi)


def r5_scripts(
    table: pd.DataFrame, levels: list[int], out: Path, dpi: int, smoke: bool
) -> str | None:
    if table is None or table.empty:
        return None
    scripts = list(dict.fromkeys(table["script"]))
    fig, axes = plt.subplots(1, len(levels), figsize=(3.6 * len(levels), 4.2), sharey=True)
    axes = np.atleast_1d(axes)
    y = np.arange(len(scripts))
    for ax, c in zip(axes, levels, strict=True):
        for k, path in enumerate(("direct", "pgbouncer")):
            g = table[(table["concurrency"] == c) & (table["path"] == path)].set_index("script")
            vals = [g["p95_ms"].get(s, np.nan) for s in scripts]
            ax.barh(y + (k - 0.5) * 0.38, vals, height=0.38, color=COLORS[path], label=LABELS[path])
        ax.set_title(f"c = {c}")
        ax.set_xlabel("Latensi p95 (ms)")
        ax.grid(alpha=0.3, axis="x")
    axes[0].set_yticks(y, scripts)
    axes[0].legend(fontsize=7)
    fig.suptitle(_title("R5: latensi p95 per skrip (median antarputaran)", smoke))
    return _save(fig, out, "R5_scripts_p95", dpi)


def make_all(
    runs: pd.DataFrame,
    pairs: pd.DataFrame,
    result: dict[str, pd.DataFrame],
    levels: list[int],
    out: Path,
    cfg: dict[str, Any],
    smoke: bool,
) -> list[str]:
    dpi = cfg["analysis"]["figure_dpi"]
    profiles = [p for p in cfg["design"]["profiles"] if p in set(runs["profile"])]
    made: list[str | None] = []
    for p in profiles:
        made.append(metric_vs_concurrency(runs, p, "tps", levels, out, dpi, smoke))
        made.append(metric_vs_concurrency(runs, p, "p95_ms", levels, out, dpi, smoke))
        made.append(latency_panel(runs, p, levels, out, dpi, smoke))
    delta = result.get("07_delta_by_level")
    made.append(delta_heatmap(delta, "tps", levels, profiles, out, dpi, smoke))
    made.append(delta_heatmap(delta, "p95_ms", levels, profiles, out, dpi, smoke))
    made.append(
        memory_vs_concurrency(result.get("15_memory_by_level"), levels, profiles, out, dpi, smoke)
    )
    made.append(capacity_bars(result.get("14_capacity"), out, dpi, smoke))
    sup = result["08_support"]
    made.append(
        support_lines(
            sup,
            "pg_total_mean",
            "Koneksi backend (rata-rata)",
            "Jumlah koneksi backend PostgreSQL",
            "support_backends",
            levels,
            profiles,
            out,
            dpi,
            smoke,
        )
    )
    made.append(
        support_lines(
            sup,
            "cl_waiting_mean",
            "cl_waiting (rata-rata)",
            "Antrean klien PgBouncer (cl_waiting)",
            "support_cl_waiting",
            levels,
            profiles,
            out,
            dpi,
            smoke,
            paths=("pgbouncer",),
        )
    )
    made.append(
        support_lines(
            sup,
            "sv_active_mean",
            "sv_active (rata-rata)",
            "Koneksi server aktif PgBouncer (sv_active)",
            "support_sv_active",
            levels,
            profiles,
            out,
            dpi,
            smoke,
            paths=("pgbouncer",),
        )
    )
    made.append(  # Subbab 3.5.2 (v4): R3/R4 row-lock contention vs R1/R2/R5
        support_lines(
            sup,
            "lock_waiting_mean",
            "Sesi menunggu lock (rata-rata)",
            "Sesi PostgreSQL yang menunggu lock",
            "support_lock_waiting",
            levels,
            profiles,
            out,
            dpi,
            smoke,
        )
    )
    for cname in ("postgres", "pgbouncer", "client"):
        made.append(
            support_lines(
                sup,
                f"cpu_cores_by_container:pgb-{cname}",
                "CPU (inti)",
                f"Pemakaian CPU kontainer {cname}",
                f"support_cpu_{cname}",
                levels,
                profiles,
                out,
                dpi,
                smoke,
            )
        )
    made.append(r5_scripts(result.get("10_r5_scripts"), levels, out, dpi, smoke))
    return [m for m in made if m]
