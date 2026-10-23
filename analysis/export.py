"""`make export-thesis`: copy thesis-ready tables and figures with Indonesian captions.

Results go to Bab 5 (Hasil dan Pembahasan) in proposal v5. Numbering (Tabel 5.x /
Gambar 5.x) is only a suggestion: renumber in Word as needed.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pandas as pd

from analysis.report import n

TABLE_CAPTIONS = {
    "05_hypotheses": "Hasil pengujian hipotesis H1 dan H2 (uji kontras satu sisi pada model "
    "faktorial, koreksi Holm)",
    "02_descriptives": "Statistik deskriptif throughput dan latensi per profil, tingkat "
    "konkurensi, dan jalur koneksi (rata-rata, SK 95%, median, IQR)",
    "07_delta_by_level": "Δ% dan selang kepercayaan 95% per profil dan tingkat konkurensi",
    "06_anova": "ANOVA dua faktor dengan pengulangan (jalur, konkurensi, interaksi) beserta "
    "partial eta squared",
    "13_memory_per_connection": "Memori privat per koneksi pada kedua jalur",
    "14_capacity": "Kapasitas koneksi pada anggaran memori yang sama",
    "15_memory_by_level": "Memori koneksi dan throughput per tingkat konkurensi",
    "04_shapiro": "Uji normalitas Shapiro-Wilk atas residu model",
    "04_levene": "Uji homogenitas varians Levene antarsel",
    "08_support": "Data pendukung: koneksi backend, antrean PgBouncer, dan pemakaian CPU",
    "09_flags": "Ringkasan flag validitas per satuan pengukuran",
    "10_r5_scripts": "Latensi per skrip profil R5",
    "11_lock_contention": "Sesi yang menunggu lock dan deadlock per profil, tingkat "
    "konkurensi, dan jalur koneksi",
    "12_commands": "Perintah pgbench, port tujuan, dan skrip profil request pada setiap "
    "satuan pengukuran (putaran pertama)",
}

FIGURE_CAPTIONS = {
    "tps_vs_concurrency": "Throughput terhadap tingkat konkurensi pada profil {p}",
    "p95_ms_vs_concurrency": "Latensi p95 terhadap tingkat konkurensi pada profil {p}",
    "latency_percentiles": "Latensi p50, p95, dan p99 pada profil {p}",
    "heatmap_delta_tps": "Δ% throughput per profil dan tingkat konkurensi",
    "heatmap_delta_p95_ms": "Δ% latensi p95 per profil dan tingkat konkurensi",
    "memory_vs_concurrency": "Memori privat untuk koneksi terhadap tingkat konkurensi",
    "capacity_by_budget": "Kapasitas koneksi pada anggaran memori yang sama",
    "support_backends": "Jumlah koneksi backend PostgreSQL pada kedua jalur",
    "support_cl_waiting": "Panjang antrean klien PgBouncer (cl_waiting)",
    "support_sv_active": "Koneksi server aktif PgBouncer (sv_active)",
    "support_lock_waiting": "Jumlah sesi PostgreSQL yang menunggu lock pada kedua jalur",
    "support_cpu_postgres": "Pemakaian CPU kontainer PostgreSQL",
    "support_cpu_pgbouncer": "Pemakaian CPU kontainer PgBouncer",
    "support_cpu_client": "Pemakaian CPU kontainer klien (pgbench)",
    "R5_scripts_p95": "Latensi p95 per skrip profil R5",
}


def _caption_for(stem: str) -> str:
    for key, text in FIGURE_CAPTIONS.items():
        if stem.endswith(key) or stem == key:
            profile = stem.split("_")[0]
            return text.format(p=profile)
    return stem


def _format_df(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        if pd.api.types.is_float_dtype(out[col]):
            out[col] = out[col].map(lambda v: n(v, 3))
    return out


def export(analysis_dir: Path) -> Path:
    thesis = analysis_dir.parent / "thesis"
    (thesis / "gambar").mkdir(parents=True, exist_ok=True)
    (thesis / "tabel").mkdir(parents=True, exist_ok=True)
    lines = [
        "# Bahan Bab 5 (tabel dan gambar)",
        "",
        "Penomoran hanya usulan. Angka pada tabel memakai koma desimal.",
        "",
        "## Tabel",
        "",
    ]
    with pd.ExcelWriter(thesis / "tabel" / "tabel_bab5.xlsx") as xw:
        i = 0
        for name, caption in TABLE_CAPTIONS.items():
            src = analysis_dir / "tables" / f"{name}.csv"
            if not src.exists():
                continue
            try:
                df = _format_df(pd.read_csv(src))
            except pd.errors.EmptyDataError:
                continue  # for example an empty table on smoke data
            i += 1
            df.to_csv(thesis / "tabel" / f"Tabel_5_{i}_{name}.csv", index=False, sep=";")
            df.to_excel(xw, sheet_name=f"Tabel 5.{i}", index=False)
            lines.append(f"- **Tabel 5.{i}** {caption} (`tabel/Tabel_5_{i}_{name}.csv`)")
    lines += ["", "## Gambar", ""]
    for i, png in enumerate(sorted((analysis_dir / "figures").glob("*.png")), start=1):
        shutil.copy2(png, thesis / "gambar" / f"Gambar_5_{i}_{png.name}")
        svg = png.with_suffix(".svg")
        if svg.exists():
            shutil.copy2(svg, thesis / "gambar" / f"Gambar_5_{i}_{svg.name}")
        lines.append(
            f"- **Gambar 5.{i}** {_caption_for(png.stem)} (`gambar/Gambar_5_{i}_{png.name}`)"
        )
    (thesis / "CAPTIONS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return thesis
