# Deviations from the proposal

Every departure from the proposal ("Skripsi-Revisi Wedanta (Revisi P0)", Bab 1–3)
or from the build prompt. Each one is toggleable in `experiment.yaml`, and the
thesis text (Bab 3 / Bab 4 threats to validity) must mention it.

| ID | What changed | Proposal / prompt | Reason | Toggle | Status |
|---|---|---|---|---|---|
| DEV-001 | The system under test is an emulated **2 vCPU / 4 GB server**. Derived settings follow the smaller budget: `shared_buffers` ≈ 25 % of the postgres memory, `maintenance_work_mem = 256MB` (prompt: 1 GB), `huge_pages = off` (prompt: try), `max_wal_size = 8GB` (prompt: start at 32 GB), no swap. | Prompt: PostgreSQL gets "the rest" of an ≥ 8-core / 32 GB host. | Weda wants a realistic small production server. | `server.*`, `postgres.*` | Decided by Weda 2026-10-01; needs supervisor sign-off |
| DEV-002 | pgbench **scale factor 100** (≈ 1.5 GB). | Scale factor 1000 (≈ 15 GB). | 15 GB on a 4 GB server makes both paths disk-bound and hides the connection-handling effect under study. | `pgbench.scale` | Decided by Weda 2026-10-01; needs supervisor sign-off |
| DEV-003 | PgBouncer runs on the **same cpuset** as PostgreSQL and its memory comes out of the 4 GB server budget. | Prompt §3.2: PgBouncer gets its own physical core. | Its own core would give the treatment path 3 vCPU against 2 for the baseline, biasing results in favor of PgBouncer. On a single VPS, PgBouncer shares the machine. `pgbouncer_cpu_saturated` is still measured against one core. | `server.colocate_pgbouncer` | Decided by Weda 2026-10-01 |
| DEV-004 | Reset the database before **every** run, including R1. | Reset before profiles that write data. | R1 would otherwise start from whatever state the previous write run left behind. | `reset.policy` | D4 |
| DEV-005 | The 20 (profile × concurrency) cells are **shuffled** inside each round. | Only path order randomized each round. | Spreads time-dependent drift (thermal, autovacuum, fragmentation) across cells. | `design.shuffle_cells_within_round` | D6 |
| DEV-006 | PgBouncer `listen_backlog` matched to the PostgreSQL backlog; identical `somaxconn` on both paths. | Not specified. | Keeps the connect path symmetric (matters for R4 at c = 1000). | `pgbouncer.match_listen_backlog` | D10 |
| DEV-007 | `pg_prewarm` of the profile's tables and indexes after each reset. | Not specified (60 s warmup only). | Equalizes cache state after the template copy (Raasveldt et al., 2018). | `reset.prewarm` | D5 |
| DEV-008 | Development and smoke host is **Windows 11 + Docker Desktop (WSL2)**. | Linux test host. | The laptop is what is available. Threats: cpusets pin WSL2 vCPUs, not guaranteed physical cores; Windows background load; laptop thermal throttling over a long campaign. | — | **Open**: decide the campaign host before M8 |
