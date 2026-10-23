# Decisions

Answers to the open design questions (Section 13 of the original build prompt) plus the
host questions. First decided by Weda on 2026-10-01 ("use defaults"); revised on
2026-10-06 when the playground was aligned with proposal v2 (transaction mode only, the
proposal text as the default); revised on 2026-10-20 for proposal v4 ("Revisi Bimbingan
13 Okt": scale factor 100, lock waits and deadlocks recorded per run); revised on
2026-10-23 for proposal v5 ("Revisi Bimbingan 20 Okt", supervisor Pak Nanang): five
gradual concurrency levels starting at 1, memory per connection and capacity in RM2, and a
short campaign of 2 to 3 hours requested by Weda.

## Host and server envelope

| # | Question | Decision | Consequence |
|---|---|---|---|
| Q0 | Where does the stack run? | Develop and smoke-test on Weda's laptop (Windows 11 + Docker Desktop/WSL2, Ryzen 7 6800H 8C/16T, 15.3 GB RAM, NVMe Micron 2450 1 TB). The host for the real campaign is decided **before calibration**. | DEV-008 (open) |
| Q1 | What is "the server"? | **Proposal text** (2026-10-06): every container gets its own cores (Subbab 3.4); PostgreSQL gets what remains after host, monitoring, PgBouncer and client. The 2 vCPU / 4 GB "small server" is kept as a switch. | `resources.layout: dedicated`; DEV-001 off |
| Q2 | pgbench scale factor | **100** (Tabel 3.7 v4, 2026-10-20; was 1000 in v2). About 1.5 GB, in memory, so `effective_spindles = 0`. R3/R4 row-lock contention on `pgbench_branches` above c = 100 is recorded and compared with R1/R2/R5 (Subbab 3.5.2). | DEV-002 retired; DEV-018 |
| Q3 | Where does PgBouncer run? | **Its own physical core** (Subbab 3.4, single-threaded process). | DEV-003 off |

## Section 13

| # | Decision | Value in `experiment.yaml` |
|---|---|---|
| D1 | Cores in the pool-size formula = **physical cores of the PostgreSQL cpuset**. On the 8-core laptop with the dedicated layout: 3 cores, so P = 2 x 3 + 0 = **6** (checked in calibration against 3 and 12). | `pgbouncer.core_basis` |
| D2 | Effective spindles = **0**: the scale-100 dataset is fully in memory (Subbab 3.3.1 and Tabel 3.2, v4; PostgreSQL Wiki: 0 when the active data is cached). | `pgbouncer.effective_spindles: 0` |
| D3 | Calibration check 4: P's median TPS >= 95 % of the best **and** P's median p95 <= 110 % of the best, at c = 100 and 1000, profiles **R3 and R5**. | `calibration.*` |
| D4 | Reset **only before profiles that write data** (proposal text). | `reset.policy: write_profiles_only` (DEV-004 off) |
| D5 | **No** `pg_prewarm`; the 10 s warmup equalizes the cache (proposal text). | `reset.prewarm: false` (DEV-007 off) |
| D6 | **Fixed** cell order; the path order is randomized in every round (proposal text). | `design.shuffle_cells_within_round: false` (DEV-005 off) |
| D7 | Primary analysis (v5): **two-factor full factorial with r = 3 replications** (Jain, 1991) on ln(value), per profile and metric. H1/H2 = one-sided contrasts at c = 500 and 1000 with the pooled model error (df = 20), Holm over 20. Replaces the exact Wilcoxon of v4, which cannot reach p < 0.05/20 with 3 pairs (smallest one-sided p = 1/8). | `analysis.primary_test: factorial_anova_log` |
| D8 | Effect size (v5): **partial eta squared** for the path, concurrency and interaction effects (Fritz, Morris & Richler, 2012); Δ% with a 95 % CI per cell. | `analysis.effect_size: partial_eta_squared` |
| D9 | Keep raw pgbench logs, **zstd-compressed**. | `raw_logs.keep: true` |
| D10 | **Match** listen backlog and `somaxconn` on both paths. | DEV-006 |
| D11 | Infrastructure failures retried at most once, SUT outcomes never retried, every attempt logged. | `retry.infra_max_retries: 1` |
| D12 | R5 seed sizes: 200 000 peserta, 20 kompetisi, 50 soal each, 50 % jawaban, 30 % hasil. | `r5_seed.*` (DEV-011) |
| D13 | Latest PostgreSQL **18.x** (`postgres:18-bookworm`) and latest PgBouncer **1.26.x** (1.26.0, 2026-09-23) at build time, pinned by digest at lock time. | `config/versions.env`, `config/locked/versions.json` |
| D14 | pgbench **one thread per client** to avoid the `-M prepared` deadlock behind a transaction pooler. **Confirm with the supervisor.** | `pgbench.threads: per_client` (DEV-013) |
| D15 | Concurrency (v5): **1, 10, 100, 500, 1000**. At least five gradual levels starting low (supervisor, 20 Oct); 1 is below the pool size (cost of the extra hop), 10 just above it; 500 and 1000 stay for H1/H2. Adding 50 costs 30 runs (about 30 min). | `design.concurrency` |
| D16 | Campaign length (v5): **3 rounds, 10 s warmup, 30 s measurement**, 150 runs, about 2.5 h (Weda, 23 Oct: 2 to 3 hours instead of about 70). Three is the smallest r the supervisor accepts ("paling tidak 3 kali") and gives the factorial model an error term. | `design.rounds`, `design.warmup_s`, `design.measure_s` |
| D17 | Memory (RM2 v5): private memory per connection = slope of container RSS against connections (cAdvisor `container_memory_rss`), capacity = connections that fit in 256 / 512 / 1024 MB on each path. Persistent profiles only (R4 opens a backend per transaction). | `analysis.memory`, series `mem_rss_bytes_by_container` |

## Open items

- D14: confirm with the supervisor.
- D16: if the first real runs show unstable TPS inside the 30 s window (see the 5 s
  progress lines in `measure.stdout`), raise `design.measure_s` to 60 (adds about 40 min).
- DEV-008: choose the campaign host before calibration.
- D14 / DEV-013: Tabel 3.7 in proposal v5 still says `-j` = cores of the pgbench
  container; Bab 4 (Tabel 4.5 of the v5 draft) reports the change to one thread per client.
- After the smoke campaign: check with `make evidence` that the dataset is served from
  memory (disk reads near zero during measurements), as Subbab 3.5.2 claims.
