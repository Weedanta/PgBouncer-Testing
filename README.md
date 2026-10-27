# PgBouncer Impact Playground

Research instrument for the thesis on the impact of PgBouncer (`pool_mode = transaction`)
on PostgreSQL throughput and latency (Ketut Bagus Wedanta Ananda Murti, Teknologi
Informasi, FILKOM Universitas Brawijaya). Design source: proposal "Skripsi-Revisi Wedanta
(Revisi Bimbingan 20 Okt) v5", Bab 1-3.

- **RM1**: how is the PgBouncer configuration set? → `make calibrate lock`
  (rule of Tabel 3.2, four checks of the "pengamatan awal", one documented adjustment,
  lock).
- **RM2**: what does PgBouncer do to TPS, latency and memory at c = 1, 10, 100, 500, 1000?
  → `make campaign` (2 paths × 5 profiles × 5 levels × 3 rounds = 150 runs, about 2.5 h).
  Output: `results/<C>/runs.csv`, one row per run. **The playground produces data only;**
  the statistics of Subbab 3.7 are done in an external tool (column dictionary:
  [docs/DATA-HASIL.md](docs/DATA-HASIL.md)).

**Start here (Indonesian): [docs/PANDUAN.md](docs/PANDUAN.md).**
Command reference: [docs/RUNBOOK.md](docs/RUNBOOK.md).

## Quick start

The campaign host is Windows 11 + WSL2 (decided 2026-10-23), WSL capped at 10 GB (DEV-019).
No Ubuntu in WSL yet? Start with [docs/PANDUAN-PELAKSANAAN.md](docs/PANDUAN-PELAKSANAAN.md). From Ubuntu (WSL):

```bash
S=/mnt/c/Users/<you>/OneDrive/Documents/Skripsi/Playground/scripts/wsl_run.sh
bash $S setup && bash $S smoke && bash $S data && bash $S memcheck && bash $S calibrate
bash $S lock && bash $S campaign
```

The same steps with `make`, inside the WSL copy (`~/pgbouncer-playground`):

```bash
make sync lint test                 # Python env, checks, unit tests
make probe secrets build up         # host split, passwords, images, stack
make init-data-smoke smoke          # full pipeline on a tiny dataset (labeled SMOKE)
make init-data memcheck             # real dataset (scale 100, ~1.5 GB), memory check at c = 1000
make calibrate lock                 # RM1, lock
make campaign CAMPAIGN=C1           # 150 runs (~2.5 h incl. resets), resumable -> results/C1/runs.csv
```

## Status

| Milestone | Scope | State |
|---|---|---|
| M0 | Skeleton, `experiment.yaml`, decisions | done |
| M1 | Host probe, Docker Compose stack, auth, limits | built; run `make up` on the host |
| M2 | Dataset, R5 schema/seed, template reset | built, tested on a native stack |
| M3 | Workloads R1-R5, session-feature linter | built, tested on a native stack |
| M4 | Monitoring (Prometheus, Grafana, exporters) | built; verify with `make check-metrics` |
| M5 | Runner (plan, run, flags, resume) | built, tested on a native stack |
| M6 | Analysis pipeline | removed 2026-10-27: statistics are done outside the repo |
| M7 | Smoke campaign and validation | run `make smoke` on the host |
| M8 | Calibration and lock | run on the campaign host |

"Tested on a native stack" means: PostgreSQL 16 + PgBouncer 1.26.0 + pgbench without
Docker, smoke-size data. The Docker stack itself is first started on the test host.

## Where things are

- `experiment.yaml`: every design parameter (defaults = proposal text)
- `docs/DEVIATIONS.md`: every implementation detail and departure, with reasons
- `docs/DECISIONS.md`: answers to the open design questions
- `docs/THESIS_MAPPING.md`: which output feeds which thesis table or figure
- `workloads/r5/README.md`: R5 scripts and the two processes left out (Tabel 2.2)
