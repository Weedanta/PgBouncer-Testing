"""Before/after snapshots of PostgreSQL and PgBouncer statistics, plus a live sampler.

Snapshots give exact per-run deltas (commits, deadlocks, checkpoints, PgBouncer wait
time). The sampler records backend and pool counts every few seconds during the
measurement, so the pooling evidence (Subbab 3.3 and 3.5: sv_active <= pool size,
cl_waiting > 0, 1000 backends on the direct path) does not depend on exporter metric
names. It also counts the client backends waiting for a lock (`wait_event_type = 'Lock'`:
row locks, tuple locks, advisory locks), which Tabel 3.8 (v4) requires to separate R3/R4
row-lock contention on pgbench_branches from connection handling (Subbab 3.5.2).
postgres_exporter has no built-in series for this, so the sampler is its source (DEV-018).
It uses one extra connection to the `postgres` database and one to the
PgBouncer admin console, identically on both paths.
"""

from __future__ import annotations

import csv
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

from playground import db
from playground.settings import Endpoints


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def snapshot(ep: Endpoints, dbname: str, include_config: bool = False) -> dict[str, Any]:
    snap: dict[str, Any] = {"taken_utc": datetime.now(UTC).isoformat()}
    with db.superuser(ep) as conn:
        snap["postmaster_start"] = str(
            db.rows(conn, "SELECT pg_postmaster_start_time() AS t")[0]["t"]
        )
        snap["pg_stat_database"] = db.rows(
            conn, "SELECT * FROM pg_stat_database WHERE datname = %s", (dbname,)
        )
        snap["pg_stat_wal"] = _try(conn, "SELECT * FROM pg_stat_wal")
        snap["pg_stat_checkpointer"] = _try(conn, "SELECT * FROM pg_stat_checkpointer")
        snap["pg_stat_bgwriter"] = _try(conn, "SELECT * FROM pg_stat_bgwriter")
        snap["pg_stat_io"] = _try(
            conn,
            "SELECT backend_type, object, context, reads, writes, extends, hits, evictions, fsyncs "
            "FROM pg_stat_io WHERE reads > 0 OR writes > 0 OR hits > 0",
        )
        if include_config:
            snap["pg_settings"] = db.rows(
                conn,
                "SELECT name, setting, unit, source FROM pg_settings "
                "WHERE source NOT IN ('default', 'override') ORDER BY name",
            )
            snap["pg_version"] = db.rows(conn, "SELECT version() AS v")[0]["v"]
    with db.pgb_admin(ep) as admin:
        snap["pgb_pools"] = db.admin_rows(admin, "SHOW POOLS")
        snap["pgb_stats"] = db.admin_rows(admin, "SHOW STATS")
        if include_config:
            snap["pgb_config"] = db.admin_rows(admin, "SHOW CONFIG")
            snap["pgb_version"] = db.admin_rows(admin, "SHOW VERSION")
    return snap


def _try(conn: psycopg.Connection, sql: str) -> list[dict[str, Any]] | None:
    try:
        return db.rows(conn, sql)
    except psycopg.Error:
        return None


def _checkpoints(snap: dict[str, Any]) -> float | None:
    if snap.get("pg_stat_checkpointer"):
        row = snap["pg_stat_checkpointer"][0]
        return _num(row.get("num_timed")) + _num(row.get("num_requested"))
    if snap.get("pg_stat_bgwriter"):
        row = snap["pg_stat_bgwriter"][0]
        if "checkpoints_timed" in row:
            return _num(row.get("checkpoints_timed")) + _num(row.get("checkpoints_req"))
    return None


def _pgb_stats_row(snap: dict[str, Any], dbname: str) -> dict[str, Any] | None:
    for row in snap.get("pgb_stats", []):
        if row.get("database") == dbname:
            return row
    return None


def deltas(before: dict[str, Any], after: dict[str, Any], dbname: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    b = (before.get("pg_stat_database") or [{}])[0]
    a = (after.get("pg_stat_database") or [{}])[0]
    for key in (
        "xact_commit",
        "xact_rollback",
        "deadlocks",
        "blks_hit",
        "blks_read",
        "conflicts",
        "temp_files",
        "temp_bytes",
        "sessions",
        "sessions_abandoned",
    ):
        if key in a and key in b:
            out[f"pg_{key}"] = _num(a[key]) - _num(b[key])
    hit, read = out.get("pg_blks_hit", 0.0), out.get("pg_blks_read", 0.0)
    out["pg_cache_hit_ratio"] = hit / (hit + read) if hit + read > 0 else None
    cb, ca = _checkpoints(before), _checkpoints(after)
    out["checkpoints"] = None if cb is None or ca is None else ca - cb
    if before.get("pg_stat_wal") and after.get("pg_stat_wal"):
        out["wal_bytes"] = _num(after["pg_stat_wal"][0].get("wal_bytes")) - _num(
            before["pg_stat_wal"][0].get("wal_bytes")
        )
    sb, sa = _pgb_stats_row(before, dbname), _pgb_stats_row(after, dbname)
    if sb and sa:
        dx = _num(sa["total_xact_count"]) - _num(sb["total_xact_count"])
        dw = _num(sa["total_wait_time"]) - _num(sb["total_wait_time"])
        out["pgb_xact_count"] = dx
        out["pgb_wait_time_us"] = dw
        out["pgb_avg_wait_us"] = dw / dx if dx > 0 else None
    out["postmaster_restarted"] = before.get("postmaster_start") != after.get("postmaster_start")
    return out


class Sampler(threading.Thread):
    """Samples pg_stat_activity and SHOW POOLS for one database into a CSV file."""

    FIELDS = [
        "ts_utc",
        "pg_total",
        "pg_active",
        "pg_idle",
        "pg_idle_in_tx",
        "pg_other",
        "pg_lock_waiting",
        "cl_active",
        "cl_waiting",
        "sv_active",
        "sv_idle",
        "sv_used",
        "sv_login",
        "maxwait_us",
        "error",
    ]

    def __init__(self, ep: Endpoints, dbname: str, out: Path, interval_s: float) -> None:
        super().__init__(daemon=True)
        self.ep, self.dbname, self.out, self.interval = ep, dbname, out, interval_s
        self._halt = threading.Event()

    def stop(self) -> None:
        self._halt.set()
        self.join(timeout=30)

    def run(self) -> None:
        with open(self.out, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=self.FIELDS)
            writer.writeheader()
            pg = admin = None
            while not self._halt.is_set():
                row: dict[str, Any] = {"ts_utc": datetime.now(UTC).isoformat()}
                try:
                    if pg is None:
                        pg = psycopg.connect(
                            db.conninfo(
                                self.ep,
                                "direct",
                                "postgres",
                                "postgres",
                                db.secret(self.ep, "postgres_password"),
                            ),
                            autocommit=True,
                        )
                    if admin is None:
                        admin = psycopg.connect(
                            db.conninfo(
                                self.ep,
                                "pgbouncer",
                                "pgbouncer",
                                "pgb_stats",
                                db.secret(self.ep, "pgb_stats_password"),
                            ),
                            autocommit=True,
                            cursor_factory=psycopg.ClientCursor,
                        )
                    counts = db.rows(
                        pg,
                        "SELECT coalesce(state, 'none') AS state, count(*) AS n, "
                        "count(*) FILTER (WHERE wait_event_type = 'Lock') AS lock_waiting "
                        "FROM pg_stat_activity WHERE datname = %s "
                        "AND backend_type = 'client backend' GROUP BY 1",
                        (self.dbname,),
                    )
                    by_state = {r["state"]: int(r["n"]) for r in counts}
                    row["pg_lock_waiting"] = sum(int(r["lock_waiting"]) for r in counts)
                    row["pg_total"] = sum(by_state.values())
                    row["pg_active"] = by_state.pop("active", 0)
                    row["pg_idle"] = by_state.pop("idle", 0)
                    row["pg_idle_in_tx"] = by_state.pop("idle in transaction", 0)
                    row["pg_other"] = sum(by_state.values())
                    for pool in db.admin_rows(admin, "SHOW POOLS"):
                        if pool.get("database") == self.dbname and pool.get("user") == "bench":
                            for key in (
                                "cl_active",
                                "cl_waiting",
                                "sv_active",
                                "sv_idle",
                                "sv_used",
                                "sv_login",
                            ):
                                row[key] = pool.get(key)
                            row["maxwait_us"] = int(
                                _num(pool.get("maxwait")) * 1e6 + _num(pool.get("maxwait_us"))
                            )
                except psycopg.Error as exc:  # record, reconnect next time
                    row["error"] = str(exc).splitlines()[0][:200]
                    pg = admin = None
                writer.writerow(row)
                fh.flush()
                self._halt.wait(self.interval)
            for conn in (pg, admin):
                if conn is not None:
                    conn.close()


def wait_s(start: float, seconds: float) -> None:
    remaining = seconds - (time.monotonic() - start)
    if remaining > 0:
        time.sleep(remaining)
