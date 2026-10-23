"""REPORT.md (for Bab 5) and PRACTITIONER_SUMMARY.md, in Indonesian.

Every sentence here is generated from the tables; nothing is interpreted beyond what
the numbers say (direction, size, significance). Interpretation belongs in Bab 5 and is
Weda's to write.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


def n(x: Any, nd: int = 1) -> str:
    """Indonesian number format: 1.234,5."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "-"
    s = f"{x:,.{nd}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def pv(p: float) -> str:
    if p is None or (isinstance(p, float) and math.isnan(p)):
        return "-"
    return "< 0,001" if p < 0.001 else n(p, 3)


def signed(x: float, nd: int = 1) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "-"
    return ("+" if x > 0 else "") + n(x, nd) + "%"


def _sign_changes(values: list[tuple[int, float]]) -> list[str]:
    out = []
    for (c1, v1), (c2, v2) in zip(values, values[1:], strict=False):
        if np.sign(v1) != np.sign(v2) and not (math.isnan(v1) or math.isnan(v2)):
            out.append(f"berubah tanda antara c = {c1} ({signed(v1)}) dan c = {c2} ({signed(v2)})")
    return out


def report_md(
    info: dict[str, Any],
    cfg: dict[str, Any],
    r: dict[str, pd.DataFrame],
    levels: list[int],
    high: list[int],
    figs: list[str],
    smoke: bool,
) -> str:
    runs = r["01_per_run"]
    hyp = r["05_hypotheses"]
    hyp_clean = r["05_hypotheses_without_flagged"]
    delta = r["07_delta_by_level"]
    aov = r["06_anova"]
    lines = ["# Laporan analisis dampak PgBouncer (Bab 5)", ""]
    if smoke:
        lines += [
            "> **SMOKE**: data uji coba pipeline (dataset kecil, durasi pendek). "
            "Jangan dipakai di skripsi.",
            "",
        ]
    df_e = int(hyp["df"].iloc[0]) if not hyp.empty else None
    lines += [
        f"Kampanye `{info['campaign_id']}`: {len(runs)} run, "
        f"{int(runs['flagged'].sum())} run bertanda (flag). Run bertanda tetap dipakai pada "
        "analisis utama; tabel sensitivitas tanpa pasangan bertanda ada di "
        "`tables/05_hypotheses_without_flagged.csv`.",
        "",
        f"Tingkat konkurensi: {levels}. Konkurensi tinggi untuk H1 dan H2: {high}. "
        "Model: ANOVA dua faktor dengan pengulangan (jalur x konkurensi) pada ln(nilai), "
        "per profil dan metrik. Uji hipotesis: kontras satu sisi jalur PgBouncer terhadap "
        f"jalur langsung dengan galat gabungan model (df = {df_e}), koreksi Holm atas "
        f"{len(hyp)} perbandingan, α = {n(cfg['analysis']['alpha'], 2)}. Δ% adalah selisih "
        "relatif rata-rata geometrik.",
        "",
        "## 1. Pengujian hipotesis",
        "",
    ]
    for hname, label in (("H1", "throughput (TPS)"), ("H2", "latensi p95")):
        h = hyp[hyp["hypothesis"] == hname] if not hyp.empty else hyp
        if h.empty:
            continue
        rejected = int((h["decision"] == "H0 ditolak").sum())
        lines += [
            f"### {hname}: {label}",
            "",
            f"H0{hname[-1]} ditolak pada **{rejected} dari {len(h)}** perbandingan.",
            "",
            "| Profil | c | Langsung | PgBouncer | Δ% | SK 95% | t | p | p Holm | Keputusan |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        nd = 0 if hname == "H1" else 2
        for _, x in h.iterrows():
            lines.append(
                f"| {x['profile']} | {x['concurrency']} | {n(x['gmean_direct'], nd)} | "
                f"{n(x['gmean_pgbouncer'], nd)} | {signed(x['delta_pct'])} | "
                f"[{signed(x['ci_low'])}; {signed(x['ci_high'])}] | {n(x['t'], 2)} | "
                f"{pv(x['p_raw'])} | {pv(x['p_holm'])} | {x['decision']} |"
            )
        lines.append("")
    if not hyp.empty and not hyp_clean.empty:
        merged = hyp.merge(
            hyp_clean, on=["hypothesis", "profile", "concurrency"], suffixes=("", "_clean")
        )
        changed = merged[merged["decision"] != merged["decision_clean"]]
        lines += [
            "Sensitivitas: tanpa pasangan yang memuat run bertanda, "
            + (
                f"keputusan berubah pada {len(changed)} perbandingan: "
                + ", ".join(
                    f"{a} {b} c={c}"
                    for a, b, c in changed[["hypothesis", "profile", "concurrency"]].itertuples(
                        index=False
                    )
                )
                if len(changed)
                else "tidak ada keputusan yang berubah"
            )
            + ".",
            "",
        ]
    lines += ["## 2. Pola dampak terhadap tingkat konkurensi", ""]
    for metric, label in (("tps", "TPS"), ("p95_ms", "latensi p95")):
        lines += [
            f"### Δ% {label}",
            "",
            "| Profil | "
            + " | ".join(f"c = {c}" for c in levels)
            + " | F interaksi | p | partial η² |",
            "|---|" + "---|" * len(levels) + "---|---|---|",
        ]
        notes = []
        for profile in cfg["design"]["profiles"]:
            b = (
                delta[(delta["profile"] == profile) & (delta["metric"] == metric)]
                if not delta.empty
                else delta
            )
            if b.empty:
                continue
            vals = [
                (
                    c,
                    float(b[b["concurrency"] == c]["delta_pct"].iloc[0])
                    if not b[b["concurrency"] == c].empty
                    else float("nan"),
                )
                for c in levels
            ]
            a = (
                aov[
                    (aov["profile"] == profile)
                    & (aov["metric"] == metric)
                    & (aov["effect"] == "jalur x konkurensi")
                ]
                if not aov.empty
                else aov
            )
            f = a["F"].iloc[0] if not a.empty else float("nan")
            fp = a["p"].iloc[0] if not a.empty else float("nan")
            eta = a["partial_eta2"].iloc[0] if not a.empty else float("nan")
            lines.append(
                f"| {profile} | "
                + " | ".join(signed(v) for _, v in vals)
                + f" | {n(f, 2)} | {pv(fp)} | {n(eta, 2)} |"
            )
            for sc in _sign_changes(vals):
                notes.append(f"- {profile}: Δ% {label} {sc}.")
        lines += [""] + (notes or ["- Tidak ada perubahan tanda Δ% antartaraf."]) + [""]
    sh, lv = r["04_shapiro"], r["04_levene"]
    if not sh.empty:
        lines += [
            "## 3. Uji prasyarat",
            "",
            f"Shapiro-Wilk pada residu model: {int((sh['p'] < 0.05).sum())} dari "
            f"{int(sh['p'].notna().sum())} kombinasi profil dan metrik menolak normalitas "
            f"(p < 0,05). Levene antarsel: {int((lv['p'] < 0.05).sum())} dari "
            f"{int(lv['p'].notna().sum())} kombinasi menolak kesamaan varians. Kombinasi yang "
            "menolak dicatat dan ditafsirkan dengan hati-hati (Subbab 3.8).",
            "",
        ]
    sup = r["08_support"]
    if not sup.empty and "pg_total_max" in sup:
        top = max(levels)
        s = sup[sup["concurrency"] == top]
        lines += [
            "## 4. Data pendukung pada konkurensi tertinggi",
            "",
            "| Profil | Jalur | Backend maks | sv_active rata-rata | cl_waiting rata-rata |",
            "|---|---|---|---|---|",
        ]
        for _, x in s.sort_values(["profile", "path"]).iterrows():
            lines.append(
                f"| {x['profile']} | {x['path']} | {n(x.get('pg_total_max'), 0)} | "
                f"{n(x.get('sv_active_mean'), 1)} | {n(x.get('cl_waiting_mean'), 1)} |"
            )
        lines.append("")
    lines += _memory_section(r, levels, cfg["pgbouncer"]["max_client_conn"])
    lines += _lock_section(info, cfg, r.get("11_lock_contention"), levels, smoke)
    fl = r["09_flags"]
    flag_cols = [c for c in fl.columns if c.startswith("flag_")]
    totals = {c: int(fl[c].sum()) for c in flag_cols}
    lines += ["## 7. Flag validitas", "", "| Flag | Jumlah run |", "|---|---|"]
    lines += [f"| `{k}` | {v} |" for k, v in totals.items()]
    lines += [
        "",
        "## 8. Berkas",
        "",
        "Tabel (CSV dan `tables.xlsx`): `tables/`. Gambar (PNG "
        f"{cfg['analysis']['figure_dpi']} dpi dan SVG): `figures/`.",
        "",
    ]
    lines += [f"- `figures/{f}.png`" for f in figs]
    return "\n".join(lines) + "\n"


def _memory_section(
    r: dict[str, pd.DataFrame], levels: list[int], cfg_max_client: int | str = "-"
) -> list[str]:
    """RM2 v5: memory per connection and capacity at an equal memory budget."""
    mf = r.get("13_memory_per_connection")
    cap = r.get("14_capacity")
    if mf is None or mf.empty:
        return []
    lines = [
        "## 5. Memori per koneksi dan kapasitas",
        "",
        "Memori per koneksi adalah kemiringan garis regresi RSS terhadap jumlah koneksi: RSS "
        "PostgreSQL terhadap jumlah backend pada jalur langsung, dan RSS PgBouncer terhadap "
        "jumlah koneksi klien pada jalur PgBouncer. RSS tidak memuat *shared_buffers* dan "
        "*page cache*.",
        "",
        "| Profil | Persisten | MB per backend (langsung) | R² | kB per klien (PgBouncer) | R² "
        "| Backend di belakang pool |",
        "|---|---|---|---|---|---|---|",
    ]
    for _, x in mf.iterrows():
        lines.append(
            f"| {x['profile']} | {'ya' if x['persistent'] else 'tidak'} | "
            f"{n(x['direct_mb_per_backend'], 2)} | {n(x['direct_r2'], 2)} | "
            f"{n(x['pgb_mb_per_client'] * 1024, 1)} | {n(x['pgb_r2'], 2)} | "
            f"{n(x['pool_backends_mean'], 1)} |"
        )
    lines.append("")
    if cap is not None and not cap.empty:
        top = max(levels)
        lines += [
            "Jumlah koneksi klien yang muat pada anggaran memori yang sama (median antarprofil "
            f"berkoneksi persisten). Nilai di atas {top} adalah ekstrapolasi dari garis regresi, "
            "bukan hasil pengukuran langsung. Angka ini hanya batas dari sisi memori: pada "
            "konfigurasi yang diuji, jalur PgBouncer tetap dibatasi *max_client_conn* "
            f"({cfg_max_client}) dan jalur langsung dibatasi *max_connections*.",
            "",
            "| Anggaran | Jalur langsung | Jalur PgBouncer | Rasio |",
            "|---|---|---|---|",
        ]
        med = cap.groupby("budget_mb")[["direct", "pgbouncer", "ratio"]].median()
        for budget, x in med.iterrows():
            lines.append(
                f"| {n(budget, 0)} MB | {n(x['direct'], 0)} | {n(x['pgbouncer'], 0)} | "
                f"{n(x['ratio'], 1)}x |"
            )
        lines.append("")
    return lines


def _lock_section(
    info: dict[str, Any],
    cfg: dict[str, Any],
    lk: pd.DataFrame | None,
    levels: list[int],
    smoke: bool,
) -> list[str]:
    """Subbab 3.5.2 (v4): lock waits and deadlocks, R3/R4 next to R1/R2/R5."""
    if lk is None or lk.empty:
        return []
    exp = info.get("experiment", cfg)
    scale = exp["datasets"]["smoke" if smoke else "campaign"]["scale"]
    lines = [
        "## 6. Perebutan lock (Subbab 3.5.2)",
        "",
        f"Dengan scale factor {scale}, tabel `pgbench_branches` berisi {scale} baris. Profil "
        "yang memperbaruinya di setiap transaksi (kolom kedua) menghadapi perebutan lock baris "
        "ketika konkurensi melampaui jumlah baris tersebut. Nilai berikut adalah median "
        "antarputaran dari rata-rata sesi yang menunggu lock selama pengukuran (sampler 5 "
        "detik), dan median jumlah deadlock per run. Data lengkap: "
        "`tables/11_lock_contention.csv`.",
        "",
        "| Profil | Memperbarui pgbench_branches | c | Menunggu lock (langsung) | "
        "Menunggu lock (PgBouncer) | Deadlock (langsung) | Deadlock (PgBouncer) |",
        "|---|---|---|---|---|---|---|",
    ]
    for profile in [p for p in cfg["design"]["profiles"] if p in set(lk["profile"])]:
        for c in levels:
            g = lk[(lk["profile"] == profile) & (lk["concurrency"] == c)].set_index("path")
            if g.empty:
                continue

            def v(path: str, col: str, nd: int, g: pd.DataFrame = g) -> str:
                return n(g[col].get(path, float("nan")), nd)

            upd = "ya" if bool(g["updates_branches"].iloc[0]) else "tidak"
            lines.append(
                f"| {profile} | {upd} | {c} | {v('direct', 'lock_waiting_median', 1)} | "
                f"{v('pgbouncer', 'lock_waiting_median', 1)} | "
                f"{v('direct', 'deadlocks_median', 0)} | {v('pgbouncer', 'deadlocks_median', 0)} |"
            )
    top = max(levels)
    t = lk[lk["concurrency"] == top]
    summary = []
    for path, label in (("direct", "jalur langsung"), ("pgbouncer", "jalur PgBouncer")):
        s = t[t["path"] == path]
        upd = s[s["updates_branches"]]["lock_waiting_median"].mean()
        other = s[~s["updates_branches"].astype(bool)]["lock_waiting_median"].mean()
        summary.append(
            f"pada {label}, rata-rata sesi menunggu lock {n(upd, 1)} untuk profil yang "
            f"memperbarui `pgbench_branches` dan {n(other, 1)} untuk profil lainnya"
        )
    lines += ["", f"Pada c = {top}: " + "; ".join(summary) + ".", ""]
    return lines


def practitioner_md(
    info: dict[str, Any],
    cfg: dict[str, Any],
    r: dict[str, pd.DataFrame],
    levels: list[int],
    smoke: bool,
) -> str:
    delta = r["07_delta_by_level"]
    sup = r["08_support"]
    top = max(levels)
    lines = ["# Ringkasan teknis untuk praktisi", ""]
    if smoke:
        lines += ["> **SMOKE**: hanya uji coba pipeline, bukan hasil penelitian.", ""]
    lines += [
        "Pengukuran membandingkan PostgreSQL yang diakses langsung dengan PostgreSQL yang "
        "diakses melalui PgBouncer mode transaction (konfigurasi terkunci), pada beban "
        "simulasi pgbench di satu host uji. Angka absolut tidak dapat dipindahkan begitu saja ke "
        "sistem produksi; yang dapat dibawa adalah arah dan pola perubahannya.",
        "",
        f"## Perubahan waktu respons dan throughput pada {top} koneksi",
        "",
        "| Profil | Δ% TPS | Δ% latensi p95 |",
        "|---|---|---|",
    ]
    for profile in cfg["design"]["profiles"]:
        if delta.empty:
            break
        t = delta[
            (delta["profile"] == profile)
            & (delta["metric"] == "tps")
            & (delta["concurrency"] == top)
        ]
        p = delta[
            (delta["profile"] == profile)
            & (delta["metric"] == "p95_ms")
            & (delta["concurrency"] == top)
        ]
        if t.empty or p.empty:
            continue
        lines.append(
            f"| {profile} ({cfg['profiles'][profile]['description']}) | "
            f"{signed(t['delta_pct'].iloc[0])} | {signed(p['delta_pct'].iloc[0])} |"
        )
    cap = r.get("14_capacity")
    if cap is not None and not cap.empty:
        med = cap.groupby("budget_mb")[["direct", "pgbouncer"]].median()
        lines += ["", "## Kapasitas koneksi pada anggaran memori yang sama", ""]
        for budget, x in med.iterrows():
            lines.append(
                f"- {n(budget, 0)} MB: sekitar {n(x['direct'], 0)} koneksi langsung, atau "
                f"{n(x['pgbouncer'], 0)} koneksi klien melalui PgBouncer."
            )
    if not sup.empty and "pg_total_max" in sup:
        s = sup[sup["concurrency"] == top]
        d = s[s["path"] == "direct"]["pg_total_max"].max()
        b = s[s["path"] == "pgbouncer"]["pg_total_max"].max()
        lines += [
            "",
            "## Efisiensi koneksi",
            "",
            f"Pada {top} klien, jumlah koneksi backend PostgreSQL tertinggi yang teramati: "
            f"{n(d, 0)} pada jalur langsung dan {n(b, 0)} pada jalur PgBouncer.",
        ]
    lines += [
        "",
        "## Batasan fitur pada mode transaction",
        "",
        "Fitur berikut tidak boleh dipakai aplikasi di belakang PgBouncer mode transaction "
        "(Tabel 2.2): `SET`/`RESET` tingkat sesi (pakai `SET LOCAL`), `LISTEN`, `PREPARE`/"
        "`DEALLOCATE` level-SQL (prepared statement level-protokol tetap boleh), cursor `WITH "
        "HOLD`, temporary table selain `ON COMMIT DROP`, `LOAD`, dan advisory lock tingkat sesi "
        "(`pg_advisory_lock()`; pakai `pg_advisory_xact_lock()`). Sebagian pelanggaran tidak "
        "memunculkan error sama sekali, misalnya advisory lock dan `LISTEN`.",
        "",
    ]
    return "\n".join(lines) + "\n"
