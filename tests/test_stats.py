"""Statistics of Subbab 3.6 (proposal v5) on SYNTHETIC data with known answers."""

import math

import numpy as np
import pandas as pd
import pytest

from analysis import stats
from analysis.pipeline import hypotheses
from playground.config import load_experiment


def test_holm_known_vector():
    p = [0.01, 0.04, 0.03, 0.005]
    assert stats.holm(p) == pytest.approx([0.03, 0.06, 0.06, 0.02])


def _balanced(a_eff=0.0, b_eff=None, ab=None, r=3, noise=0.0, seed=0):
    rng = np.random.default_rng(seed)
    b_eff = b_eff or [0.0, 1.0, 2.0]
    rows = []
    for i, path in enumerate(("direct", "pgbouncer")):
        for j, c in enumerate((1, 10, 100)[: len(b_eff)]):
            for k in range(r):
                y = 5 + (a_eff if i else 0.0) + b_eff[j] + (ab[j] if (ab and i) else 0.0)
                rows.append(
                    {"path": path, "concurrency": c, "round": k, "y": y + rng.normal(0, noise)}
                )
    return pd.DataFrame(rows)


def test_factorial_anova_matches_statsmodels_on_balanced_data():
    import statsmodels.formula.api as smf
    from statsmodels.stats.anova import anova_lm

    df = _balanced(a_eff=0.3, ab=[0.0, 0.2, 0.5], noise=0.1, seed=4)
    fit = stats.factorial_anova(df)
    assert fit["ok"] and fit["method"] == "balanced"
    assert fit["df_e"] == 2 * 3 * (3 - 1)
    dd = df.assign(conc=df["concurrency"].astype(str))
    ref = anova_lm(smf.ols("y ~ C(path, Sum) * C(conc, Sum)", data=dd).fit(), typ=2)
    rows = {r["effect"]: r for r in fit["rows"]}
    assert rows["jalur"]["SS"] == pytest.approx(ref.loc["C(path, Sum)", "sum_sq"])
    assert rows["konkurensi"]["SS"] == pytest.approx(ref.loc["C(conc, Sum)", "sum_sq"])
    assert rows["jalur x konkurensi"]["SS"] == pytest.approx(
        ref.loc["C(path, Sum):C(conc, Sum)", "sum_sq"]
    )
    assert rows["galat"]["SS"] == pytest.approx(ref.loc["Residual", "sum_sq"])
    assert rows["jalur x konkurensi"]["p"] < 0.05  # clear synthetic interaction
    eta = rows["jalur x konkurensi"]["partial_eta2"]
    assert 0 < eta < 1


def test_factorial_anova_unbalanced_falls_back_to_type3():
    df = _balanced(a_eff=0.3, noise=0.1, seed=2).iloc[1:]  # one run missing
    fit = stats.factorial_anova(df)
    assert fit["ok"] and fit["method"].startswith("type III")


def test_factorial_anova_needs_replications():
    df = _balanced(r=1)
    assert not stats.factorial_anova(df)["ok"]


def test_contrast_delta_is_ratio_of_geometric_means():
    pgb = np.log([110.0, 110.0, 110.0])
    direct = np.log([100.0, 100.0, 100.0])
    res = stats.contrast(pgb, direct, mse=0.0001, df_e=20, alternative="greater")
    assert res["delta_pct"] == pytest.approx(10.0)
    assert res["ci_low"] < 10.0 < res["ci_high"]
    assert res["p"] < 0.001
    less = stats.contrast(pgb, direct, mse=0.0001, df_e=20, alternative="less")
    assert less["p"] > 0.99


def test_contrast_with_n3_and_df20_can_pass_holm_for_20_comparisons():
    """With r = 3 the pooled error (df = 20) still allows p below 0.05 / 20."""
    rng = np.random.default_rng(1)
    pgb = np.log(1.10 * (1 + rng.normal(0, 0.01, 3)))
    direct = np.log(1 + rng.normal(0, 0.01, 3))
    res = stats.contrast(pgb, direct, mse=0.01**2, df_e=20, alternative="greater")
    assert res["p"] < 0.05 / 20


def test_linear_fit_and_capacity():
    x = np.array([1, 10, 100, 500, 1000] * 3, dtype=float)
    y = 50 + 2.0 * x  # synthetic: 2 MB per backend
    fit = stats.linear_fit(x, y)
    assert fit["slope"] == pytest.approx(2.0) and fit["r2"] == pytest.approx(1.0)
    cap = stats.capacity(1024, 2.0, 2 / 1024, pool_backends=6, pgb_base_mb=4)
    assert cap["direct"] == 512
    assert cap["pgbouncer"] == math.floor((1024 - 12 - 4) / (2 / 1024))
    bad = stats.capacity(1024, float("nan"), 0.002, 6, 4)
    assert math.isnan(bad["direct"]) and math.isnan(bad["pgbouncer"])


def test_describe_t_interval():
    x = np.array([10.0, 12.0, 11.0, 13.0, 9.0])
    d = stats.describe(x)
    assert d["mean"] == 11.0
    assert d["ci_low"] < 11.0 < d["ci_high"]


def synthetic_runs(cfg):
    """SYNTHETIC runs: PgBouncer +10 % TPS and -20 % p95 at every level, 1 % noise."""
    rng = np.random.default_rng(5)
    rows = []
    for profile in cfg["design"]["profiles"]:
        for c in cfg["design"]["concurrency"]:
            for rnd in range(1, cfg["design"]["rounds"] + 1):
                for path, mult_t, mult_l in (("direct", 1.0, 1.0), ("pgbouncer", 1.1, 0.8)):
                    rows.append(
                        {
                            "profile": profile,
                            "concurrency": c,
                            "round": rnd,
                            "pair_id": f"r{rnd}-{profile}-c{c}",
                            "path": path,
                            "tps": 1000 * mult_t * (1 + rng.normal(0, 0.01)),
                            "p95_ms": 50 * mult_l * (1 + rng.normal(0, 0.01)),
                            "lat_mean_ms": 10.0 * (1 + rng.normal(0, 0.01)),
                            "p50_ms": 5.0 * (1 + rng.normal(0, 0.01)),
                            "p99_ms": 80.0 * (1 + rng.normal(0, 0.01)),
                        }
                    )
    return pd.DataFrame(rows)


def test_hypothesis_table_rejects_on_clear_synthetic_effect():
    cfg = load_experiment()
    h = hypotheses(synthetic_runs(cfg), cfg, [500, 1000])
    assert len(h) == 20  # family of 20 comparisons
    assert (h["df"] == 20).all()  # 2 x 5 x (3 - 1)
    assert (h["decision"] == "H0 ditolak").all()
    assert h.loc[h["hypothesis"] == "H1", "delta_pct"].between(8, 12).all()
    assert h.loc[h["hypothesis"] == "H2", "delta_pct"].between(-22, -18).all()
