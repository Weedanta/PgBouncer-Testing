# Deviations from the proposal

Reference text: proposal "Skripsi-Revisi Wedanta (Revisi Bimbingan 20 Okt) v5", Bab 1-3
(aligned 2026-10-23; before that: "(Revisi Bimbingan 13 Okt) v4" and "(Revisi Mode
Transaction) v2").
`experiment.yaml` follows that text literally by default. This file lists (A) every
implementation detail the proposal does not specify or had to be changed to make the
design run, and (B) optional switches that depart from the proposal and are **off**.
Anything in (A) belongs in the thesis (Bab 3 implementation notes or Subbab 3.8 threats
to validity) and should be shown to the supervisor.

## A. Active (implementation details and necessary changes)

| ID | What | Proposal says | Why | Where |
|---|---|---|---|---|
| DEV-013 | **pgbench `-j` = `-c` (one thread per client), on both paths.** | Tabel 3.7: `-j` = cores of the pgbench container. | With `-M prepared`, pgbench prepares each statement with a *blocking* `PQprepare()` the first time a client uses it. Behind a transaction pooler with more clients than pool slots, one client waiting for a pooled server blocks its whole pgbench thread, including the other clients of that thread that hold the pool's servers in the middle of a transaction: nobody can finish, and after `query_wait_timeout` (120 s) PgBouncer drops them. Reproduced with pgbench 16.15 and with the pgbench 18.6 source (`prepareCommand()`), against PgBouncer 1.22.0 and 1.26.0: every multi-statement profile (R2, R3, R4, the multi-statement R5 scripts) hangs; one thread per client removes the hang. `-M prepared` (the treatment-relevant setting, tied to `max_prepared_statements`) is kept. Threat: thread overhead in the client container; watched by `flag_client_cpu_saturated`. **Discuss with the supervisor.** Alternatives kept as switches: `pgbench.threads: cpuset` (literal proposal, hangs) or keep `cpuset` and change `-M prepared` to `-M extended`. | `pgbench.threads` |
| DEV-006 | PgBouncer `listen_backlog` = 2 x `max_connections` (2200), same order as PostgreSQL's own backlog; identical `somaxconn` on both paths. | Not specified. | Keeps the connect path symmetric (matters for R4 at c = 1000). | `pgbouncer.match_listen_backlog`, D10 |
| DEV-009 | PostgreSQL parameters other than `max_connections`: `shared_buffers` 25 % and `effective_cache_size` 70 % of the PostgreSQL container memory, `work_mem` 4 MB, `maintenance_work_mem` min(1 GB, mem/8), `max_wal_size` 32 GB, `checkpoint_timeout` 30 min, durability on. | "shared_buffers ... bernilai sama pada semua perlakuan" without values. | Values are needed; they are identical on both paths and locked. | `postgres.*`, `config/active/postgresql.conf` |
| DEV-010 | pgbench runs with `-n` (no automatic vacuum/truncate before a run). | Not specified. | pgbench's default pre-run vacuum truncates `pgbench_history` and vacuums tables, which changes the state between paths. State is controlled by the reset procedure instead. | `pgbench.no_vacuum` |
| DEV-011 | R5 seed sizes (200 000 peserta, 20 kompetisi, 50 soal, 50 % jawaban, 30 % hasil) and the leaderboard timeout literal `SET LOCAL statement_timeout = '30s'`. | Tabel 3.6 gives scripts and weights only. | Sizes are a design assumption, locked with the workloads. The timeout must be a literal (bind parameters are not allowed in `SET`) and generous so it never fires. | `r5_seed`, `workloads/r5/` |
| DEV-012 | Calibration runs use a 5 s warmup before the 30 s check. | "pengujian singkat berdurasi 30 detik" (Subbab 3.3.2 v5). | Avoids judging pool sizes on a cold cache. Calibration data is never performance data. | `calibration.warmup_s` |
| DEV-014 | Warmup and measurement are two pgbench processes with identical commands; the primary TPS is pgbench's own reported TPS; the per-transaction log must contain exactly the transactions pgbench counted (`flag_log_count_mismatch`). | "Pemanasan 60 detik (tidak dicatat), pengukuran 300 detik". | Only the 300 s measurement writes `--log`; percentiles come from the log; TPS follows pgbench's definition (Subbab 2.6). | `playground/runner.py` |
| DEV-015 | Before every run (both paths) PgBouncer's pooled server connections are closed with `KILL`/`RESUME` (PgBouncer keeps running). The runner keeps one extra connection to the `postgres` database and one to the PgBouncer admin console during measurement (5 s sampler). | Not specified. | Without the KILL, a direct-path run shares PostgreSQL with idle pooled backends left by the previous PgBouncer run. The sampler gives backend/pool counts independently of exporter metric names; it is identical on both paths. | `data.pre_run`, `snapshots.Sampler` |
| DEV-016 | Smoke tests use a separate database `bench_smoke` (scale 10) with its own PgBouncer entry. | Not specified. | Smoke runs never touch the real dataset. | `datasets.smoke` |
| DEV-017 | PgBouncer is installed from the PGDG apt repository inside the official PostgreSQL image (newest `1.26.x`), not built from the release tarball. | "Versi stabil terbaru ... dikunci". | Same Debian base as PostgreSQL; the exact package version and image digest are recorded at lock time. | `docker/pgbouncer.Dockerfile`, `config/versions.env` |
| DEV-018 | "Sesi yang menunggu lock" (Tabel 3.8, v4) is counted by the runner's 5 s sampler from `pg_stat_activity` (`wait_event_type = 'Lock'`, client backends of the run's database), not by postgres_exporter. Per run: `lock_waiting_mean`, `lock_waiting_max`, and `deadlocks` (delta of `pg_stat_database.deadlocks`) in `runs.csv`. | Tabel 3.8 lists it under postgres_exporter (pg_stat_activity). | postgres_exporter has no built-in series for sessions waiting on a lock (`pg_locks_count` counts locks by mode, not waiters, and misses row locks). The sampler reads the same view, on both paths, every 5 s. Deadlocks are also exported from postgres_exporter (`pg_deadlocks` series). Counted lock waits include row, tuple and advisory locks (R5 `nomor_peserta.sql` uses `pg_advisory_xact_lock`). | `snapshots.Sampler`, `runner.py`, `analysis/pipeline.lock_contention` |
| DEV-008 | Development and smoke host is Windows 11 + Docker Desktop (WSL2). | Linux test host (Tabel 3.9). | The laptop is what is available. Threats: cpusets pin WSL2 vCPUs, not guaranteed physical cores; Windows background load; laptop thermal throttling over a ~40 h campaign. **Open: decide the campaign host before calibration.** | - |

Note on scale factor 100 (Tabel 3.7 and Subbab 3.5.2, v4): the proposal itself now sets
scale 100 (about 1.5 GB, in memory), so it is not a deviation of the playground. The
proposal records it as a deliberate departure from the pgbench guidance (scale >= clients):
R3/R4 update one of only 100 `pgbench_branches` rows per transaction. The playground backs
the two claims the proposal makes about it: (1) the dataset is in memory (`make probe`
warns otherwise; `disk_read_mb_s_mean` and `cache_hit_ratio` per run; `make evidence`), and
(2) lock waits and deadlocks are recorded per run and compared between R3/R4 and R1/R2/R5
(`tables/11_lock_contention.csv`, `figures/support_lock_waiting.png`, REPORT section 5).

## Design changes in proposal v5 (not deviations)

The supervisor meeting of 20 Oct 2026 and Weda's request of 23 Oct changed the proposal
itself, so these are proposal text now, not departures from it:

- Concurrency 1, 10, 100, 500, 1000 (five gradual levels starting low) instead of
  100, 200, 500, 1000.
- 3 rounds, 10 s warmup, 30 s measurement: 150 runs, about 2.5 h, instead of 400 runs and
  about 40 h. Calibration checks are 30 s.
- Analysis: two-factor ANOVA with replications on ln values, contrasts with the pooled
  error and Holm, partial eta squared. The exact Wilcoxon of v4 needs about 10 pairs.
- RM2 now covers memory: RSS per container, memory per connection, capacity at an equal
  memory budget (`mem_rss_bytes_by_container`, tables 13 to 15).
- Thesis structure: Bab 4 Perancangan dan Implementasi, Bab 5 Hasil dan Pembahasan,
  Bab 6 Penutup.

## B. Optional switches (OFF by default, decided 2026-10-01, superseded 2026-10-06)

Weda first chose a "small server" design (2026-10-01). On 2026-10-06 the playground was
aligned with proposal v2; the earlier choices stay available as switches. On 2026-10-20
proposal v4 adopted scale 100 itself, so DEV-002 is retired (it is now the proposal text). Turning one on
is a design change: get the supervisor's approval and record it here.

| ID | Switch | Effect when on |
|---|---|---|
| DEV-001 | `resources.layout: small_server` | PostgreSQL emulates a 2 vCPU / 4 GB server (one physical core + sibling). |
| DEV-003 | (part of `small_server`) | PgBouncer shares the server cpuset instead of its own core. |
| DEV-002 | retired 2026-10-20 | Scale 100 is the proposal v4 default (Tabel 3.7). Scale 1000 would now be the deviation. |
| DEV-004 | `reset.policy: every_run` | Reset also before R1 runs. |
| DEV-005 | `design.shuffle_cells_within_round: true` | The 20 cells are shuffled inside each round. |
| DEV-007 | `reset.prewarm: true` | `pg_prewarm` of the profile's relations after each reset. |
