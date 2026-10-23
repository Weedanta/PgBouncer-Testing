# R5: weighted online-competition mix (Tabel 3.6)

Eight pgbench scripts, run together with fixed weights (`-f file@weight`). The weights
are a design assumption (Subbab 3.5), not measurements of a production system, and are
locked together with the PgBouncer configuration (`config/locked/workloads.sha256`).

| Script | Simulated operation | Weight |
|---|---|---|
| `autosave.sql` | Automatic answer save (`INSERT ... ON CONFLICT DO UPDATE`) | 35 % |
| `ambil_soal.sql` | Fetch a question with the saved answer (`SELECT` with `JOIN`) | 25 % |
| `heartbeat.sql` | Mark the work session as alive (`UPDATE`) | 15 % |
| `login.sql` | Validate user and token (point `SELECT`) | 10 % |
| `leaderboard.sql` | Leaderboard aggregation with `SET LOCAL statement_timeout` | 5 % |
| `daftar.sql` | Participant registration (multi-statement transaction) | 4 % |
| `submit.sql` | Final submission and scoring (multi-statement transaction) | 4 % |
| `nomor_peserta.sql` | Participant number under `pg_advisory_xact_lock()` | 2 % |

Variables passed by the runner with `-D`: `peserta`, `kompetisi`, `soal`. Their values
come from `r5_seed` and the dataset's `r5_seed_fraction` in `experiment.yaml`.

## Processes intentionally left out

The proposal models ten processes of an online-competition application. Two are left
out on purpose because they depend on session-level state, which transaction mode does
not support (Tabel 2.2, Subbab 2.4 and 3.5):

1. **Locking a work session with `pg_advisory_lock()`.** A session-scoped advisory lock
   sticks to the pooled backend, which is handed to another client right after the
   transaction ends. The call succeeds without an error, but the exclusivity the
   application relies on is gone.
2. **Result notification with `LISTEN`.** The `LISTEN` registration lives on the
   backend and is lost when the backend returns to the pool. The client gets no error;
   it just stops receiving notifications.

Two processes were kept by rewriting them in a transaction-safe form:

- `nomor_peserta.sql` uses the transaction-scoped `pg_advisory_xact_lock()`.
- `leaderboard.sql` sets its timeout with `SET LOCAL`, which ends with the transaction.

`tools/lint_session_features.py` checks every workload file for the constructs of
Tabel 2.2 and fails if one is used (run by `make lint` and before calibration).

## Data

Schema: `workloads/sql/r5_schema.sql`. Seed: `workloads/sql/r5_seed.sql` (fixed
`setseed`, serial plan, so the same seed always gives the same rows). Post-run checks:
`workloads/sql/r5_invariants.sql` (participant numbers have no gaps, every `hasil` row
points at an existing session).
