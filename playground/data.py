"""Dataset initialization and the reset procedure (Subbab 3.4, "Kendali Variabel").

init-data builds a *template* database once: pgbench tables at the configured scale,
the R5 schema and seed, VACUUM ANALYZE, CHECKPOINT. The template is then frozen
(IS_TEMPLATE, no connections). Every reset recreates the test database from it with
CREATE DATABASE ... TEMPLATE, so every measurement unit starts from the same state,
without restarting PostgreSQL or PgBouncer (a restart would empty the caches).
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

from playground import db
from playground.config import REPO_ROOT, results_dir
from playground.settings import Endpoints

SQL_DIR = REPO_ROOT / "workloads" / "sql"

# Relations touched by each profile (used only when reset.prewarm is on, DEV-007).
PGBENCH_RELS = [
    "pgbench_accounts",
    "pgbench_accounts_pkey",
    "pgbench_branches",
    "pgbench_branches_pkey",
    "pgbench_tellers",
    "pgbench_tellers_pkey",
    "pgbench_history",
]
R5_RELS = [
    "r5.peserta",
    "r5.peserta_pkey",
    "r5.token_sesi",
    "r5.token_sesi_pkey",
    "r5.kompetisi",
    "r5.soal",
    "r5.soal_pkey",
    "r5.sesi_pengerjaan",
    "r5.sesi_pengerjaan_pkey",
    "r5.jawaban",
    "r5.jawaban_pkey",
    "r5.pendaftaran",
    "r5.pendaftaran_pkey",
    "r5.hasil",
    "r5.hasil_pkey",
    "r5.hasil_leaderboard_idx",
    "r5.nomor_counter",
    "r5.nomor_peserta",
]


def _log(msg: str) -> None:
    print(f"[{datetime.now(UTC).strftime('%H:%M:%S')}] {msg}", flush=True)


def _pg_env(ep: Endpoints, user_secret: str) -> dict[str, str]:
    env = dict(os.environ)
    env["PGPASSWORD"] = db.secret(ep, user_secret)
    return env


def _run(cmd: list[str], env: dict[str, str]) -> float:
    t0 = time.monotonic()
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:3])} failed:\n{proc.stdout}\n{proc.stderr}")
    return time.monotonic() - t0


def dataset_names(cfg: dict[str, Any], dataset: str) -> tuple[str, str]:
    ds = cfg["datasets"][dataset]
    return ds["database"], ds["template"]


def init_data(cfg: dict[str, Any], ep: Endpoints, dataset: str) -> dict[str, Any]:
    dbname, template = dataset_names(cfg, dataset)
    ds = cfg["datasets"][dataset]
    scale = ds["scale"]
    seed = cfg["r5_seed"]
    frac = ds["r5_seed_fraction"]
    report: dict[str, Any] = {
        "dataset": dataset,
        "database": dbname,
        "template": template,
        "scale": scale,
        "started_utc": datetime.now(UTC).isoformat(),
        "steps": {},
    }
    _log(f"init-data {dataset}: dropping old {dbname}/{template}")
    with db.superuser(ep) as conn:
        for name in (dbname, template):
            if _db_exists(conn, name):
                conn.execute(
                    f'ALTER DATABASE "{name}" WITH IS_TEMPLATE false ALLOW_CONNECTIONS true'
                )
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{template}" OWNER bench')
    with db.superuser(ep, template) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_stat_statements")
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_prewarm")

    host, port = ep.host_port("direct")
    bench_env = _pg_env(ep, "bench_password")
    _log(f"pgbench -i -s {scale} -I {cfg['pgbench']['init_steps']} (this can take a while)")
    report["steps"]["pgbench_init_s"] = _run(
        [
            "pgbench",
            "-i",
            "-s",
            str(scale),
            "-I",
            cfg["pgbench"]["init_steps"],
            "-h",
            host,
            "-p",
            str(port),
            "-U",
            "bench",
            template,
        ],
        bench_env,
    )
    psql = [
        "psql",
        "-X",
        "-q",
        "-v",
        "ON_ERROR_STOP=1",
        "-h",
        host,
        "-p",
        str(port),
        "-U",
        "bench",
        "-d",
        template,
    ]
    _log("R5 schema")
    report["steps"]["r5_schema_s"] = _run(
        psql
        + [
            "-v",
            f"sesi_fillfactor={seed['sesi_fillfactor']}",
            "-f",
            str(SQL_DIR / "r5_schema.sql"),
        ],
        bench_env,
    )
    peserta = max(1, int(seed["peserta"] * frac))
    _log(f"R5 seed ({peserta} peserta)")
    report["steps"]["r5_seed_s"] = _run(
        psql
        + [
            "-v",
            f"seed={seed['random_seed']}",
            "-v",
            f"peserta={peserta}",
            "-v",
            f"kompetisi={seed['kompetisi']}",
            "-v",
            f"soal={seed['soal_per_kompetisi']}",
            "-v",
            f"jawaban_fraction={seed['jawaban_fraction']}",
            "-v",
            f"hasil_fraction={seed['hasil_fraction']}",
            "-f",
            str(SQL_DIR / "r5_seed.sql"),
        ],
        bench_env,
    )
    report["r5_sizes"] = {
        "peserta": peserta,
        "kompetisi": seed["kompetisi"],
        "soal_per_kompetisi": seed["soal_per_kompetisi"],
    }
    _log("VACUUM (ANALYZE) + CHECKPOINT")
    t0 = time.monotonic()
    with db.superuser(ep, template) as conn:
        conn.execute("VACUUM (ANALYZE)")
        report["table_sizes"] = db.rows(
            conn,
            """SELECT n.nspname || '.' || c.relname AS relation,
                      pg_total_relation_size(c.oid) AS bytes, c.reltuples::bigint AS est_rows
               FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE c.relkind = 'r' AND n.nspname IN ('public', 'r5')
               ORDER BY bytes DESC""",
        )
        report["database_bytes"] = db.rows(
            conn, "SELECT pg_database_size(current_database()) AS b"
        )[0]["b"]
    with db.superuser(ep) as conn:
        conn.execute("CHECKPOINT")
        conn.execute(f'ALTER DATABASE "{template}" WITH ALLOW_CONNECTIONS false IS_TEMPLATE true')
    report["steps"]["vacuum_checkpoint_s"] = time.monotonic() - t0
    _log("first reset (creates the test database from the template)")
    report["steps"]["first_reset_s"] = reset(cfg, ep, dataset, profile=None)
    report["finished_utc"] = datetime.now(UTC).isoformat()
    out = (
        results_dir()
        / "datasets"
        / f"{dataset}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    _log(f"done; report: {out}")
    return report


def _db_exists(conn: psycopg.Connection, name: str) -> bool:
    return bool(db.rows(conn, "SELECT 1 AS x FROM pg_database WHERE datname = %s", (name,)))


def reset(cfg: dict[str, Any], ep: Endpoints, dataset: str, profile: str | None) -> float:
    """Recreate the test database from its template. PgBouncer stays up the whole time."""
    dbname, template = dataset_names(cfg, dataset)
    strategy = cfg["reset"]["create_strategy"]
    t0 = time.monotonic()
    with db.pgb_admin(ep) as admin:
        try:
            db.admin_command(admin, f"KILL {dbname}")  # drop pooled server connections
        except psycopg.Error:
            pass  # database not yet known to PgBouncer
        try:
            with db.superuser(ep) as conn:
                conn.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
                conn.execute(
                    f'CREATE DATABASE "{dbname}" TEMPLATE "{template}" '
                    f"STRATEGY {strategy} OWNER bench"
                )
        finally:
            try:
                db.admin_command(admin, f"RESUME {dbname}")
            except psycopg.Error:
                pass  # was not paused/killed
    if cfg["reset"]["prewarm"] and profile is not None:
        prewarm(cfg, ep, dbname, profile)
    return time.monotonic() - t0


def prewarm(cfg: dict[str, Any], ep: Endpoints, dbname: str, profile: str) -> None:
    rels = R5_RELS if profile == "R5" else PGBENCH_RELS
    with db.superuser(ep, dbname) as conn:
        for rel in rels:
            conn.execute("SELECT pg_prewarm(%s::regclass)", (rel,))


def pre_run(ep: Endpoints, dbname: str) -> None:
    """Before every run: CHECKPOINT and statistics reset (Subbab 3.4).

    PgBouncer's idle pooled server connections are closed first (KILL + RESUME), so a
    direct-path run does not share PostgreSQL with leftover pooler backends and both
    paths start with the same set of backends. PgBouncer itself keeps running.
    """
    with db.pgb_admin(ep) as admin:
        try:
            db.admin_command(admin, f"KILL {dbname}")
            db.admin_command(admin, f"RESUME {dbname}")
        except psycopg.Error:
            pass
    with db.superuser(ep) as conn:
        conn.execute("CHECKPOINT")
        for target in ("bgwriter", "checkpointer", "io", "wal"):
            try:
                conn.execute("SELECT pg_stat_reset_shared(%s)", (target,))
            except psycopg.Error:
                pass  # target not available on this PostgreSQL version
    with db.superuser(ep, dbname) as conn:
        conn.execute("SELECT pg_stat_reset()")
        try:
            conn.execute("SELECT pg_stat_statements_reset()")
        except psycopg.Error:
            pass


def r5_invariants(ep: Endpoints, dbname: str) -> list[dict[str, Any]]:
    sql = (SQL_DIR / "r5_invariants.sql").read_text(encoding="utf-8")
    with db.superuser(ep, dbname) as conn:
        return db.rows(conn, sql)


def dataset_ready(ep: Endpoints, cfg: dict[str, Any], dataset: str) -> bool:
    dbname, template = dataset_names(cfg, dataset)
    with db.superuser(ep) as conn:
        return _db_exists(conn, template) and _db_exists(conn, dbname)


def results_path(*parts: str) -> Path:
    return results_dir().joinpath(*parts)
