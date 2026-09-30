# Output → thesis mapping

Every item the thesis reports, and where the playground produces it.
Status is updated as milestones land.

| Thesis item | Content | Produced by | Status |
|---|---|---|---|
| Tabel 3.2 | Final PgBouncer configuration and its basis | `results/calibration/<ts>/REPORT.md`, `config/locked/pgbouncer.ini` | M8 |
| Tabel 3.4 | Implemented components and versions | `config/locked/versions.json`, `docker-compose.yml` | M1 / M8 |
| Tabel 3.5 | Request profiles R1–R5 | `experiment.yaml` (`profiles`), `workloads/` | M3 |
| Tabel 3.6 | R5 scripts and weights | `workloads/r5/*.sql`, `experiment.yaml` | M3 |
| Tabel 3.7 | Test parameters | `experiment.yaml` (`design`, `pgbench`) | M0 |
| Tabel 3.8 | Metrics and their sources | `config/prometheus/`, run `meta.json` | M4 |
| Bab 4 (RM1) | Calibration evidence and adjustments | `results/calibration/<ts>/REPORT.md` | M8 |
| Bab 4 (RM2) | Descriptive tables, hypothesis table, Friedman/post hoc, figures | `results/<campaign>/analysis/`, `make export-thesis` | M6 |
| Bab 5 | Where the impact turns positive / reverses / saturates | `results/<campaign>/analysis/REPORT.md` | M6 |
| Lampiran | Configs, scripts, versions, host spec | `config/locked/`, `workloads/`, `docs/HOST.md` | M8 |
| Practitioner session | Short technical brief | `results/<campaign>/analysis/PRACTITIONER_SUMMARY.md` | M6 |
| Threats to validity | All deviations | `docs/DEVIATIONS.md` | ongoing |
