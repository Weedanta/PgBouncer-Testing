# Decisions

Answers to the open questions in the design, plus the host
questions raised before M0. Decided by Weda on
2026-10-01 ("use defaults" for D1–D13).

## Host and server envelope (asked before M0)

| # | Question | Decision | Consequence |
|---|---|---|---|
| Q0 | Where does the stack run? | Develop and smoke-test on Weda's laptop (Windows 11 + Docker Desktop/WSL2, Ryzen 7 6800H 8C/16T, 15.3 GB RAM, NVMe Micron 2450 1 TB). The host for the real campaign is decided **before M8**. | DEV-008 |
| Q1 | What is "the server"? | A realistic small server: **2 vCPU (one physical core + its SMT sibling), 4 GB RAM**, normal local disk. Emulated with cpuset + memory limits; the other cores run pgbench and monitoring. | DEV-001 |
| Q2 | pgbench scale factor | **100** (≈1.5 GB), so the working set fits the 4 GB server and the measured effect is connection handling, not disk I/O. Needs supervisor sign-off. | DEV-002 |
| Q3 | Where does PgBouncer run? | **Inside the server budget**, same cpuset as PostgreSQL, 128 MB carved from the 4 GB. Avoids giving the treatment path more CPU than the baseline. | DEV-003 |

## Section 13

| # | Decision | Value in `experiment.yaml` |
|---|---|---|
| D1 | Cores in the pool-size formula = **physical cores of the server cpuset** (1 physical core → P = 2×1 + 0 = **2**, checked in calibration against 1 and 4). | `pgbouncer.core_basis: server_physical_cores` |
| D2 | Effective spindles for NVMe = **0**. | `pgbouncer.effective_spindles: 0` |
| D3 | Calibration check 4: P's median TPS ≥ 95 % of the best **and** P's median p95 ≤ 110 % of the best, at c = 100 and 1000, profiles **R3 and R5**. | `calibration.*` |
| D4 | Reset the database **before every run, including R1**. | `reset.policy: every_run` (DEV-004) |
| D5 | `pg_prewarm` **on** after each reset. | `reset.prewarm: true` (DEV-007) |
| D6 | **Shuffle** the 20 cells inside each round; path order randomized inside each cell. | `design.shuffle_cells_within_round: true` (DEV-005) |
| D7 | Primary test **Wilcoxon signed-rank (exact, one-sided)**; paired t-test secondary. **Pending confirmation with the supervisor.** | `analysis.primary_test: wilcoxon_exact` |
| D8 | N in r = Z/√N = **number of pairs (10)**, per Fritz, Morris & Richler (2012). r with N = 20 is reported in the appendix for transparency. | `analysis.effect_size_n: pairs` |
| D9 | Keep raw pgbench logs, **zstd-compressed**. | `raw_logs.keep: true` |
| D10 | **Match** listen backlog and `somaxconn` on both paths. | `pgbouncer.match_listen_backlog: true` (DEV-006) |
| D11 | Retry policy of Section 8.5: infrastructure failures retried at most once, SUT outcomes never retried, every attempt logged. | `retry.infra_max_retries: 1` |
| D12 | R5 seed sizes as in Section 4.2 (200 000 peserta, 20 kompetisi, 50 soal each, …). | `r5_seed.*` |
| D13 | Use the latest PostgreSQL **18.x** and latest stable PgBouncer at build time, then pin by digest. Exact versions are filled in at M1. | recorded in `config/locked/versions.json` |

## Open items

- D7: confirm Wilcoxon-as-primary with the supervisor.
- DEV-001/DEV-002: supervisor sign-off on the 2 vCPU / 4 GB server and scale factor 100.
- DEV-008: choose the campaign host before M8.
