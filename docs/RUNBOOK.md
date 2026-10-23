# Runbook

Operator guide, in order. The Indonesian walkthrough with explanations is
[PANDUAN.md](PANDUAN.md); this file is the short command reference.

## 1. One-time setup

### Windows + WSL2 (development host, DEV-008)

1. Install Ubuntu for WSL: `wsl --install -d Ubuntu-24.04`.
2. Give WSL enough memory and CPUs. Create `%UserProfile%\.wslconfig`:
   ```ini
   [wsl2]
   memory=13GB
   processors=16
   swap=0
   ```
   then `wsl --shutdown` and reopen Ubuntu. (The default is 50 % of RAM, which leaves
   PostgreSQL less than the 2 GB `make probe` requires.)
3. Docker Desktop → Settings → Resources → WSL integration → enable Ubuntu-24.04.
4. Clone the repo **inside the WSL filesystem** (for example `~/playground`), not under
   OneDrive or `/mnt/c`: PostgreSQL data and `results/` must live on the WSL ext4 disk.
5. Install uv: `curl -LsSf https://astral.sh/uv/install.sh | sh`.
6. Windows: plug the laptop in, use the "Best performance" power mode, pause OneDrive and
   Windows Update during calibration and the campaign.

### Every host

```bash
make sync          # Python 3.12 environment from uv.lock
make lint test     # static checks + unit tests (synthetic data only)
make probe         # cpusets and memory -> config/host.env, docs/HOST.md
make secrets       # passwords -> secrets/ (gitignored)
make build         # renders config/active/, pulls and builds images
make up            # starts the stack, waits until healthy
```

`make probe` prints the resource split. Read the warnings: below 8 physical cores or
32 GB it says so, and it warns when the campaign dataset (scale 100 is about 1.5 GB plus
the R5 schema) would not fit in the PostgreSQL container's memory, which Subbab 3.5.2
relies on. With scale 100 on the 8-core / 13 GB WSL laptop there is no such warning.

## 2. Data and monitoring (M2-M4)

```bash
make init-data-smoke   # scale 10, seconds
make init-data         # scale 100 (~1.5 GB) + R5: minutes; report in results/datasets/
```

Monitoring: Grafana http://127.0.0.1:3000 (user `admin`, password in
`secrets/grafana_admin_password`), Prometheus http://127.0.0.1:9090. While a smoke run is
going, run `make check-metrics`: every required series of Tabel 3.8 must be present. If
an exporter release renamed a metric, fix `config/prometheus/series.yaml`.

## 3. Smoke test and validation (M7)

```bash
make smoke                       # 60 runs at c = 1/10/50, ~40 min, full analysis, labeled SMOKE
make evidence CAMPAIGN=SMOKE-...  # pooling evidence + load-generator checks
```

Before calibration, also check the load generator at the real top level (Subbab 3.2,
"pembangkit beban tidak menjadi titik jenuh"): open Grafana, run `make calibrate` (its
c = 1000 runs) or a short manual run, and watch the client container CPU. If it is
saturated, raise `resources.client_core_share` and rerun `make probe render up`.

After the smoke campaign, `make evidence CAMPAIGN=SMOKE-...` also prints the disk read
rate during measurements (the dataset should be served from memory) and the sessions
waiting for a lock, R3/R4 against R1/R2/R5 (Subbab 3.5.2).

## 4. Calibration and lock (RM1, Subbab 3.3)

```bash
make calibrate     # step A + 4 checks, 30 s each; about 40 min at scale 100 (resets included)
# read results/calibration/<timestamp>/REPORT.md
```

- All checks pass → `make lock` (pins image digests, writes `config/locked/`, commits and
  tags `config-locked-<date>`).
- A check fails → one documented adjustment per value, then calibrate again:
  `make adjust PARAM=default_pool_size VALUE=8 REASON="..."`, `make reload`,
  `make calibrate`. A second adjustment of the same value is refused (talk to the
  supervisor).

## 5. Campaign (RM2)

```bash
make campaign CAMPAIGN=C1     # 150 runs, about 2.5 h; prints the estimate, asks to confirm
make status CAMPAIGN=C1       # progress
make resume CAMPAIGN=C1       # after any interruption
```

The runner refuses to start if anything locked changed (config, workloads,
experiment.yaml, PgBouncer's live settings). An interrupted run is restarted from its
reset step; the old attempt stays on disk as `<run_id>.attemptN` and in `attempts.csv`.

Rules while it runs: no other workloads, never restart `postgres` or `pgbouncer`
(a restart is logged as an incident), never edit `config/locked/`.

## 6. Analysis (Bab 5)

```bash
make analyze CAMPAIGN=C1          # results/C1/analysis/ (tables, figures, REPORT.md)
make export-thesis CAMPAIGN=C1    # results/C1/thesis/ (numbered, Indonesian captions)
```

## 7. Everyday commands

| Command | Purpose |
|---|---|
| `make ps`, `make logs` | Container status, PostgreSQL/PgBouncer logs |
| `make down` / `make up` | Stop / start (data stays) |
| `make reload` | PgBouncer re-reads `config/active/pgbouncer.ini` (no restart) |
| `make clean-data` | Delete the PostgreSQL data directory (asks to type DELETE) |
| `make clean-results CAMPAIGN=...` | Delete one campaign (asks to type its id) |
