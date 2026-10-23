"""Statistics of Subbab 3.6 (proposal v5). Pure functions, unit-tested on synthetic data.

Design: two-factor full factorial with r replications (Jain, 1991, two-factor design with
replications). Per profile and metric, factor A = connection path (2 levels), factor B =
concurrency (5 levels), r = 3 rounds. The model is fitted on ln(value), because both
throughput and latency change multiplicatively with concurrency (their spread grows with
their size); on the log scale a difference between two cell means is the log of a ratio.

Conventions
- d = mean ln(pgbouncer) - mean ln(direct) in one concurrency cell. Delta% = (e^d - 1) x 100
  (ratio of geometric means). Positive Delta% on TPS / negative on latency = improvement.
- H1 (TPS): one-sided "pgbouncer > direct"; H2 (p95): one-sided "pgbouncer < direct".
- Contrast test: t = d / sqrt(MSE (1/n1 + 1/n2)) with the pooled error of the whole
  factorial model, df = ab(r - 1) = 20 for a balanced campaign (Jain, 1991).
- Holm (1979) step-down adjustment over the family of 20 comparisons.
- Effect size: partial eta squared = SS_effect / (SS_effect + SS_error) (Fritz, Morris
  and Richler, 2012).
- Prerequisites: Shapiro-Wilk on the model residuals, Levene across the a x b cells.
- Memory: ordinary least squares of RSS (MB) on the number of connections; the slope is
  the private memory per connection.
"""

from __future__ import annotations

import math
import warnings
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

ALTERNATIVE = {
    "greater": "pgbouncer > direct",
    "less": "pgbouncer < direct",
    "two-sided": "pgbouncer != direct",
}


def holm(pvalues: list[float]) -> list[float]:
    """Holm-adjusted p-values (monotone, capped at 1)."""
    p = np.asarray(pvalues, dtype=float)
    m = p.size
    order = np.argsort(p)
    adj = np.empty(m)
    running = 0.0
    for rank, idx in enumerate(order):
        running = max(running, (m - rank) * p[idx])
        adj[idx] = min(1.0, running)
    return adj.tolist()


def shapiro(x: np.ndarray) -> dict[str, Any]:
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    if x.size < 3 or np.ptp(x) == 0:
        return {"W": float("nan"), "p": float("nan"), "normal": None, "n": int(x.size)}
    res = stats.shapiro(x)
    return {
        "W": float(res.statistic),
        "p": float(res.pvalue),
        "normal": bool(res.pvalue >= 0.05),
        "n": int(x.size),
    }


def levene(groups: list[np.ndarray]) -> dict[str, Any]:
    """Levene (1960), centered on the mean as in the original test."""
    groups = [np.asarray(g, dtype=float)[~np.isnan(np.asarray(g, dtype=float))] for g in groups]
    if len(groups) < 2 or any(g.size < 2 for g in groups):
        return {"F": float("nan"), "p": float("nan"), "equal_var": None}
    if all(np.ptp(g) == 0 for g in groups):
        return {"F": float("nan"), "p": float("nan"), "equal_var": None}
    res = stats.levene(*groups, center="mean")
    return {
        "F": float(res.statistic),
        "p": float(res.pvalue),
        "equal_var": bool(res.pvalue >= 0.05),
    }


def factorial_anova(df: pd.DataFrame, y: str = "y") -> dict[str, Any]:
    """Two-factor ANOVA with replications: factors `path` and `concurrency`.

    Balanced data use the textbook sums of squares (Jain, 1991). Unbalanced data (a run is
    missing) fall back to type III sums of squares of an OLS fit with sum-to-zero contrasts.
    Returns the ANOVA rows, MSE, error df, residuals and the cell table.
    """
    d = df[["path", "concurrency", y]].dropna()
    cells = d.groupby(["path", "concurrency"])[y]
    counts = cells.size()
    a = d["path"].nunique()
    b = d["concurrency"].nunique()
    out: dict[str, Any] = {"a": a, "b": b, "n": int(len(d))}
    if a < 2 or b < 2 or counts.min() < 2:
        out.update({"ok": False, "reason": "need two levels per factor and r >= 2 per cell"})
        return out
    resid = d[y] - cells.transform("mean")
    balanced = counts.nunique() == 1 and len(counts) == a * b
    if balanced:
        r = int(counts.iloc[0])
        grand = d[y].mean()
        mean_a = d.groupby("path")[y].mean()
        mean_b = d.groupby("concurrency")[y].mean()
        mean_ab = cells.mean()
        ss_a = b * r * ((mean_a - grand) ** 2).sum()
        ss_b = a * r * ((mean_b - grand) ** 2).sum()
        inter = [
            mean_ab[(i, j)] - mean_a[i] - mean_b[j] + grand
            for i in mean_a.index
            for j in mean_b.index
        ]
        ss_ab = r * float(np.sum(np.square(inter)))
        ss_e = float((resid**2).sum())
        df_a, df_b, df_ab, df_e = a - 1, b - 1, (a - 1) * (b - 1), a * b * (r - 1)
        method = "balanced"
    else:
        import statsmodels.formula.api as smf
        from statsmodels.stats.anova import anova_lm

        dd = d.rename(columns={y: "yy"}).assign(conc=lambda x: x["concurrency"].astype(str))
        model = smf.ols("yy ~ C(path, Sum) * C(conc, Sum)", data=dd).fit()
        tab = anova_lm(model, typ=3)
        ss_a = float(tab.loc["C(path, Sum)", "sum_sq"])
        ss_b = float(tab.loc["C(conc, Sum)", "sum_sq"])
        ss_ab = float(tab.loc["C(path, Sum):C(conc, Sum)", "sum_sq"])
        ss_e = float(tab.loc["Residual", "sum_sq"])
        df_a, df_b, df_ab = a - 1, b - 1, (a - 1) * (b - 1)
        df_e = int(tab.loc["Residual", "df"])
        method = "type III (unbalanced)"
    mse = ss_e / df_e if df_e > 0 else float("nan")
    rows = []
    for name, ss, dfx in (
        ("jalur", ss_a, df_a),
        ("konkurensi", ss_b, df_b),
        ("jalur x konkurensi", ss_ab, df_ab),
    ):
        ms = ss / dfx
        f = ms / mse if mse > 0 else float("nan")
        p = float(stats.f.sf(f, dfx, df_e)) if mse > 0 else float("nan")
        eta = ss / (ss + ss_e) if (ss + ss_e) > 0 else float("nan")
        rows.append(
            {
                "effect": name,
                "SS": float(ss),
                "df": int(dfx),
                "MS": float(ms),
                "F": float(f),
                "p": p,
                "partial_eta2": float(eta),
            }
        )
    rows.append(
        {
            "effect": "galat",
            "SS": ss_e,
            "df": int(df_e),
            "MS": mse,
            "F": float("nan"),
            "p": float("nan"),
            "partial_eta2": float("nan"),
        }
    )
    out.update(
        {
            "ok": True,
            "method": method,
            "rows": rows,
            "mse": mse,
            "df_e": int(df_e),
            "residuals": resid.to_numpy(),
            "cells": [g.to_numpy() for _, g in cells],
        }
    )
    return out


def contrast(
    y_pgb: np.ndarray,
    y_direct: np.ndarray,
    mse: float,
    df_e: int,
    alternative: str,
    level: float = 0.95,
) -> dict[str, Any]:
    """Contrast pgbouncer - direct in one cell, on ln values, with the pooled model error."""
    y_pgb = np.asarray(y_pgb, dtype=float)
    y_direct = np.asarray(y_direct, dtype=float)
    n1, n2 = y_pgb.size, y_direct.size
    d = float(y_pgb.mean() - y_direct.mean())
    se = math.sqrt(mse * (1 / n1 + 1 / n2)) if mse > 0 else float("nan")
    t = d / se if se and se > 0 else float("nan")
    if math.isnan(t):
        p = float("nan")
    elif alternative == "greater":
        p = float(stats.t.sf(t, df_e))
    elif alternative == "less":
        p = float(stats.t.cdf(t, df_e))
    else:
        p = float(2 * stats.t.sf(abs(t), df_e))
    half = float(stats.t.ppf(0.5 + level / 2, df_e) * se) if se == se else float("nan")
    return {
        "d_log": d,
        "se_log": se,
        "t": t,
        "df": int(df_e),
        "p": p,
        "delta_pct": (math.exp(d) - 1) * 100,
        "ci_low": (math.exp(d - half) - 1) * 100,
        "ci_high": (math.exp(d + half) - 1) * 100,
        "n_pgbouncer": int(n1),
        "n_direct": int(n2),
    }


def linear_fit(x: np.ndarray, y: np.ndarray, level: float = 0.95) -> dict[str, Any]:
    """OLS y = a + b x with a t-interval for the slope."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    ok = ~(np.isnan(x) | np.isnan(y))
    x, y = x[ok], y[ok]
    n = x.size
    if n < 3 or np.ptp(x) == 0:
        return {
            "n": int(n),
            "slope": float("nan"),
            "intercept": float("nan"),
            "r2": float("nan"),
            "slope_ci_low": float("nan"),
            "slope_ci_high": float("nan"),
        }
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = stats.linregress(x, y)
    half = float(stats.t.ppf(0.5 + level / 2, n - 2) * res.stderr)
    return {
        "n": int(n),
        "slope": float(res.slope),
        "intercept": float(res.intercept),
        "r2": float(res.rvalue**2),
        "slope_ci_low": float(res.slope - half),
        "slope_ci_high": float(res.slope + half),
    }


def capacity(
    budget_mb: float,
    per_backend_mb: float,
    per_client_pgb_mb: float,
    pool_backends: float,
    pgb_base_mb: float,
) -> dict[str, float]:
    """Connections that fit in one memory budget (RM2 v5, the supervisor's 1 GB example).

    Direct path: every client holds one backend, so N = budget / memory per backend.
    PgBouncer path: the budget pays for the P pooled backends and PgBouncer's own base
    memory first; the rest is divided by the memory per client connection in PgBouncer.
    """

    def ok(v: float) -> bool:
        return v is not None and not math.isnan(v) and v > 0

    direct = math.floor(budget_mb / per_backend_mb) if ok(per_backend_mb) else float("nan")
    if ok(per_client_pgb_mb) and ok(per_backend_mb):
        left = budget_mb - pool_backends * per_backend_mb - max(pgb_base_mb, 0.0)
        pgb = math.floor(left / per_client_pgb_mb) if left > 0 else 0
    else:
        pgb = float("nan")
    return {"budget_mb": float(budget_mb), "direct": direct, "pgbouncer": pgb}


def describe(x: np.ndarray, level: float = 0.95) -> dict[str, Any]:
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    n = x.size
    if n == 0:
        return {"n": 0}
    mean = float(x.mean())
    sd = float(x.std(ddof=1)) if n > 1 else 0.0
    half = float(stats.t.ppf(0.5 + level / 2, n - 1) * sd / math.sqrt(n)) if n > 1 else 0.0
    q1, med, q3 = np.percentile(x, [25, 50, 75])
    return {
        "n": n,
        "mean": mean,
        "sd": sd,
        "ci_low": mean - half,
        "ci_high": mean + half,
        "median": float(med),
        "q1": float(q1),
        "q3": float(q3),
        "iqr": float(q3 - q1),
    }
