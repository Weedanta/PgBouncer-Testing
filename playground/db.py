"""Database helpers: superuser, bench role and the PgBouncer admin console."""

from __future__ import annotations

import subprocess
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row

from playground.secretgen import read_secret
from playground.settings import Endpoints


def secret(ep: Endpoints, name: str) -> str:
    return read_secret(name, ep.secrets_dir)


def conninfo(ep: Endpoints, path: str, dbname: str, user: str, password: str) -> str:
    host, port = ep.host_port(path)
    return psycopg.conninfo.make_conninfo(
        host=host,
        port=port,
        dbname=dbname,
        user=user,
        password=password,
        application_name="playground-runner",
        connect_timeout=15,
    )


@contextmanager
def superuser(ep: Endpoints, dbname: str = "postgres"):
    """Direct connection as the postgres superuser (resets, snapshots, sampler)."""
    pw = secret(ep, "postgres_password")
    with psycopg.connect(
        conninfo(ep, "direct", dbname, "postgres", pw), autocommit=True, row_factory=dict_row
    ) as conn:
        yield conn


@contextmanager
def bench(ep: Endpoints, path: str, dbname: str):
    pw = secret(ep, "bench_password")
    with psycopg.connect(conninfo(ep, path, dbname, "bench", pw), autocommit=True) as conn:
        yield conn


@contextmanager
def pgb_admin(ep: Endpoints, user: str = "pgb_admin"):
    """PgBouncer admin console. It only speaks the simple query protocol, so every
    command goes through a ClientCursor (client-side binding, simple protocol)."""
    pw = secret(ep, f"{user}_password")
    conn = psycopg.connect(
        conninfo(ep, "pgbouncer", "pgbouncer", user, pw),
        autocommit=True,
        cursor_factory=psycopg.ClientCursor,
    )
    try:
        yield conn
    finally:
        conn.close()


def admin_rows(conn: psycopg.Connection, command: str) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(command, prepare=False)
        if cur.description is None:
            return []
        return [dict(r) for r in cur.fetchall()]


def admin_command(conn: psycopg.Connection, command: str) -> None:
    with conn.cursor() as cur:
        cur.execute(command, prepare=False)


def rows(conn: psycopg.Connection, sql: str, params: Any = None) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()] if cur.description else []


def pgbench_version() -> str:
    proc = subprocess.run(["pgbench", "--version"], capture_output=True, text=True, check=False)
    return proc.stdout.strip()
