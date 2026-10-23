# Output → thesis mapping

Where every thesis item comes from. All paths are relative to the repo root;
`<C>` is the campaign id, `<ts>` a calibration timestamp.

## Bab 3 (Metodologi)

| Thesis item | Content | Produced by |
|---|---|---|
| Tabel 3.1 | Variables (paths, concurrency, metrics, control variables) | `experiment.yaml` (`design`, `postgres`, `pgbouncer`) |
| Tabel 3.2 | PgBouncer parameters and their basis, final values | `results/calibration/<ts>/REPORT.md` section "Langkah 3", `config/locked/pgbouncer.ini` |
| Tabel 3.3 / 3.4 | Container choice, components and roles | `docker-compose.yml`, `docs/PANDUAN.md` |
| Gambar 3.3 | Playground architecture | `Gambar/arsitektur-playground.drawio` (outside the repo) matches `docker-compose.yml` |
| Tabel 3.5 | Profiles R1-R5 | `experiment.yaml` (`profiles`), `workloads/` |
| Tabel 3.6 | R5 scripts and weights | `workloads/r5/*.sql`, `workloads/r5/README.md` |
| Tabel 3.7 | Execution parameters (5 levels, 3 rounds, 10 s + 30 s, scale factor 100; v5) | `experiment.yaml` (`design`, `pgbench`, `datasets.campaign.scale`), `docs/DEVIATIONS.md` (DEV-013) |
| Tabel 3.8 | Metric sources (incl. lock waits, deadlocks and RSS memory per container, v5) | `config/prometheus/series.yaml` (`mem_rss_bytes_by_container`), run `meta.json`, `samples.csv` (`pg_lock_waiting`), snapshots (`deadlocks`), `docs/DEVIATIONS.md` (DEV-018) |
| Tabel 3.9 | Tools and versions | `config/locked/versions.json`, `config/locked/images.lock.json`, `docs/HOST.md` |
| Subbab 3.8 | Threats to validity | `docs/DEVIATIONS.md`, flags in `results/<C>/runs.csv` |

## Bab 4 (Perancangan dan Implementasi, proposal v5)

| Thesis item | Content | Produced by |
|---|---|---|
| Components and versions | Images, PgBouncer package, exporters | `config/versions.env`, `config/locked/versions.json` |
| Host and cpusets | Cores per container, memory | `docs/HOST.md`, `config/host.env` (`make probe`) |
| Implemented architecture and data flow | Containers, ports, where every number is stored | `docker-compose.yml`, `Gambar/arsitektur-playground.drawio` (outside the repo) |
| Run procedure | Nine steps of one measurement unit | `playground/runner.py` (module docstring) |
| pgbench commands | Command, port and scripts per measurement unit | `results/<C>/analysis/tables/12_commands.csv` (round 1; full commands in `runs/<run_id>/meta.json`) |
| Adjustments to the design | Implementation details and changes | `docs/DEVIATIONS.md` |
| RM1 answer | Rule, initial value, four checks, adjustments, final config | `results/calibration/<ts>/REPORT.md`, `compatibility_lint.md`, `config/locked/pgbouncer.ini` |

## Bab 5 (Hasil dan Pembahasan, proposal v5)

| Thesis item | Content | Produced by |
|---|---|---|
| Execution summary | Runs done, retries, flags, duration | `results/<C>/attempts.csv`, `.../tables/09_flags.csv`, `make status` |
| Descriptive tables | Mean, 95 % CI, median, IQR per profile × c × path (incl. RSS memory) | `.../tables/02_descriptives.csv` |
| Impact per pair | Δ% per round (descriptive) | `.../tables/03_impact_pairs.csv` |
| Factorial ANOVA | Path, concurrency, interaction: F, p, partial η² | `.../tables/06_anova.csv` |
| Prerequisite tests | Shapiro-Wilk on residuals, Levene across cells | `.../tables/04_shapiro.csv`, `04_levene.csv` |
| Δ% per level | Ratio of geometric means with 95 % CI | `.../tables/07_delta_by_level.csv`, `figures/heatmap_delta_*.png` |
| Hypothesis table (H1, H2) | Δ%, CI, t, p, p Holm, decision | `.../tables/05_hypotheses.csv` |
| Sensitivity | Same without flagged pairs | `.../tables/05_hypotheses_without_flagged.csv` |
| Trend figures | TPS, p95, p50/p99 vs concurrency per profile | `.../figures/<R>_tps_vs_concurrency.png`, `<R>_p95_ms_vs_concurrency.png`, `<R>_latency_percentiles.png` |
| Memory per connection (RM2 v5) | MB per backend (direct), kB per client (PgBouncer) | `.../tables/13_memory_per_connection.csv`, `figures/memory_vs_concurrency.png` |
| Capacity at an equal memory budget (RM2 v5) | Connections per 256 / 512 / 1024 MB on each path | `.../tables/14_capacity.csv`, `figures/capacity_by_budget.png` |
| Memory and throughput per level (trade-off) | Connection memory next to TPS and p95 | `.../tables/15_memory_by_level.csv` |
| R5 per script | p95 per script | `.../figures/R5_scripts_p95.png`, `tables/10_r5_scripts.csv` |
| Row-lock contention (Subbab 3.5.2) | Lock waits and deadlocks, R3/R4 vs R1/R2/R5 | `.../tables/11_lock_contention.csv`, `figures/support_lock_waiting.png`, `REPORT.md` section 6 |
| Dataset in memory (Subbab 3.5.2) | Disk reads and shared_buffers hit ratio during measurements | `runs.csv` (`disk_read_mb_s_mean`, `cache_hit_ratio`), `make evidence` |
| Where the impact turns positive, reverses, saturates | Sign changes of Δ% | `results/<C>/analysis/REPORT.md` section 2 |
| Why (supporting data) | Backends, cl_waiting, sv_active, CPU per container | `.../tables/08_support.csv`, `figures/support_*.png` |
| Practitioner feedback session | Short technical brief | `results/<C>/analysis/PRACTITIONER_SUMMARY.md` |
| Numbered copies | Tabel 5.x / Gambar 5.x with captions | `results/<C>/thesis/` (`make export-thesis`) |

## Lampiran

Locked configs (`config/locked/`), workload scripts (`workloads/`), host spec
(`docs/HOST.md`), versions (`config/locked/versions.json`), dataset report
(`results/datasets/campaign-*.json`), flags summary (`.../tables/09_flags.csv`).
