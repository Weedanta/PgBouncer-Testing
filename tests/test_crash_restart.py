"""Crash-restart detection (DEV-026), on SYNTHETIC snapshots only."""

from playground.snapshots import crash_restart, deltas


def _snap(pid, commits, wal=1000, checkpoints=0):
    return {
        "postmaster_start": "2099-01-01 00:00:00+00",
        "checkpointer_pid": pid,
        "pg_stat_database": [{"xact_commit": commits, "blks_hit": 90, "blks_read": 10}],
        "pg_stat_wal": [{"wal_bytes": wal}],
        "pg_stat_checkpointer": [{"num_timed": checkpoints, "num_requested": 0}],
        "pgb_stats": [{"database": "bench", "total_xact_count": 50, "total_wait_time": 500}],
    }


def test_new_checkpointer_pid_is_a_crash_restart():
    assert crash_restart(_snap(31, 100), _snap(5860, 5))
    assert not crash_restart(_snap(31, 100), _snap(31, 900))


def test_unknown_pid_is_not_reported_as_a_crash():
    assert not crash_restart(_snap(None, 100), _snap(5860, 5))


def test_postgres_deltas_are_void_after_a_crash_restart():
    dl = deltas(_snap(31, 100, wal=5000), _snap(5860, 5, wal=100), "bench")
    assert dl["postgres_crash_restart"] is True
    assert dl["pg_xact_commit"] is None and dl["pg_cache_hit_ratio"] is None
    assert dl["wal_bytes"] is None and dl["checkpoints"] is None
    # PgBouncer keeps its own statistics, so its deltas stay valid.
    assert dl["pgb_xact_count"] == 0


def test_normal_run_keeps_its_deltas():
    dl = deltas(_snap(31, 100), _snap(31, 900), "bench")
    assert dl["postgres_crash_restart"] is False
    assert dl["pg_xact_commit"] == 800
