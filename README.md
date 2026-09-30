# PgBouncer Impact Playground

Research instrument for the thesis *Dampak PgBouncer terhadap throughput dan latensi
PostgreSQL* (Ketut Bagus Wedanta Ananda Murti, Teknologi Informasi, FILKOM Universitas
Brawijaya).

It measures what adding PgBouncer (`pool_mode = transaction`, config fixed by a documented
calibration) in front of PostgreSQL 18 does to throughput and latency, compared with direct
connections, at client concurrency 100, 200, 500 and 1000:

- **RM1**: how is the PgBouncer configuration determined? → `make calibrate lock`
- **RM2**: what is the impact on TPS and latency? → `make campaign analyze`
  (2 paths × 5 profiles × 4 concurrency levels × 10 rounds = 400 runs)

The server under test is a realistic small server: 2 vCPU, 4 GB RAM, local NVMe disk
(see `docs/DEVIATIONS.md`).

## Status

| Milestone | Scope | State |
|---|---|---|
| M0 | Skeleton, `experiment.yaml`, decisions | ✅ |
| M1 | Host probe, Docker Compose stack, auth, limits | ⏳ |
| M2 | Dataset, R5 schema, template and reset | ⏳ |
| M3 | Workloads R1–R5, session-feature linter | ⏳ |
| M4 | Monitoring (Prometheus, Grafana, exporters) | ⏳ |
| M5 | Campaign runner (plan, run, flags, resume) | ⏳ |
| M6 | Analysis pipeline | ⏳ |
| M7 | Smoke campaign and validation | ⏳ |
| M8 | Calibration and lock | ⏳ |

## Quick start (development)

```bash
make sync       # locked Python 3.12 environment (uv)
make validate   # experiment.yaml vs. research design
make lint test
```

Full setup, including WSL2 on Windows, is in [docs/RUNBOOK.md](docs/RUNBOOK.md).

## Where things are

- `experiment.yaml`: every design parameter
- `docs/DECISIONS.md`: answers to the open design questions
- `docs/DEVIATIONS.md`: every departure from the proposal and why
- `docs/THESIS_MAPPING.md`: which output feeds which thesis table or figure
