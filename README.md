# PgBouncer Impact Playground

Research instrument for the thesis on the impact of PgBouncer (`pool_mode = transaction`)
on PostgreSQL throughput and latency (Ketut Bagus Wedanta Ananda Murti, Teknologi
Informasi, FILKOM Universitas Brawijaya). Design source: proposal "Skripsi-Revisi Wedanta
(Revisi Bimbingan 20 Okt) v5", Bab 1-3.

- **RM1**: how is the PgBouncer configuration set? → `make calibrate lock`
  (rule of Tabel 3.2, four checks of the "pengamatan awal", one documented adjustment,
  lock).
- **RM2**: what does PgBouncer do to TPS, latency and memory at c = 1, 10, 100, 500, 1000?
  → `make campaign analyze` (2 paths × 5 profiles × 5 levels × 3 rounds = 150 runs,
  about 2.5 h; two-factor ANOVA with replications on ln values, contrasts + Holm, memory
  per connection and capacity at an equal memory budget).

**Start here (Indonesian): [docs/PANDUAN.md](docs/PANDUAN.md).**
Command reference: [docs/RUNBOOK.md](docs/RUNBOOK.md).

## Quick start

```bash
make sync lint test                 # Python env, checks, unit tests
make probe secrets build up         # host split, passwords, images, stack
make init-data-smoke smoke          # full pipeline on a tiny dataset (labeled SMOKE)
make init-data calibrate lock       # real dataset (scale 100, ~1.5 GB), RM1, lock
make campaign CAMPAIGN=C1           # 150 runs (~2.5 h incl. resets), resumable
make analyze export-thesis CAMPAIGN=C1
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
| M6 | Analysis pipeline | built, tested (unit + smoke data) |
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
