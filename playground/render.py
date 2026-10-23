"""Render the service configuration (postgresql.conf, pg_hba.conf, pgbouncer.ini).

Inputs: experiment.yaml, config/host.env (from `make probe`) and, after calibration,
config/calibration_overrides.yaml (at most one documented adjustment per value).
Output: config/active/, which docker compose mounts. `make lock` freezes it into
config/locked/ and the runner compares hashes before every run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from playground.config import REPO_ROOT

ACTIVE_DIR = REPO_ROOT / "config" / "active"
OVERRIDES = REPO_ROOT / "config" / "calibration_overrides.yaml"


def load_overrides(path: Path = OVERRIDES) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


ADJUSTABLE = ("default_pool_size", "max_client_conn", "query_wait_timeout")


def _ov(overrides: dict[str, Any], key: str, default: Any) -> Any:
    ov = overrides.get(key)
    return ov["value"] if ov else default


def rule_pool_size(cfg: dict[str, Any], host: dict[str, str]) -> int:
    """Tabel 3.2: default_pool_size = (2 x physical cores) + effective spindles (D1, D2)."""
    cores = int(host["POSTGRES_PHYSICAL_CORES"])
    return 2 * cores + int(cfg["pgbouncer"]["effective_spindles"])


def pool_size(cfg: dict[str, Any], host: dict[str, str], overrides: dict[str, Any]) -> int:
    ov = overrides.get("default_pool_size")
    return int(ov["value"]) if ov else rule_pool_size(cfg, host)


def listen_backlog(cfg: dict[str, Any]) -> int:
    """D10: PostgreSQL listens with backlog 2 x max_connections (capped by somaxconn)."""
    return min(2 * cfg["postgres"]["max_connections"], cfg["net_sysctls"]["net.core.somaxconn"])


def postgres_memory(cfg: dict[str, Any], host: dict[str, str]) -> dict[str, str]:
    mem = int(host["MEM_POSTGRES_MB"])
    pg = cfg["postgres"]
    mwm = pg["maintenance_work_mem"]
    if mwm == "auto":
        mwm = f"{min(1024, mem // 8)}MB"
    return {
        "shared_buffers": f"{int(mem * pg['shared_buffers_fraction'])}MB",
        "effective_cache_size": f"{int(mem * pg['effective_cache_size_fraction'])}MB",
        "maintenance_work_mem": mwm,
    }


def render_postgresql_conf(cfg: dict[str, Any], host: dict[str, str]) -> str:
    pg = cfg["postgres"]
    mem = postgres_memory(cfg, host)
    settings = {
        "listen_addresses": "'*'",
        "port": pg["port"],
        "max_connections": pg["max_connections"],
        "password_encryption": pg["password_encryption"],
        "shared_buffers": mem["shared_buffers"],
        "effective_cache_size": mem["effective_cache_size"],
        "work_mem": pg["work_mem"],
        "maintenance_work_mem": mem["maintenance_work_mem"],
        "huge_pages": pg["huge_pages"],
        "fsync": pg["fsync"],
        "synchronous_commit": pg["synchronous_commit"],
        "full_page_writes": pg["full_page_writes"],
        "random_page_cost": pg["random_page_cost"],
        "checkpoint_timeout": pg["checkpoint_timeout"],
        "checkpoint_completion_target": pg["checkpoint_completion_target"],
        "max_wal_size": pg["max_wal_size"],
        "track_io_timing": "on",
        "track_functions": "none",
        "shared_preload_libraries": "'pg_stat_statements,pg_prewarm'",
        "pg_prewarm.autoprewarm": "off",
        "log_checkpoints": "on",
        "log_autovacuum_min_duration": "0",
        "log_connections": "off",  # would flood the log at R4 (new connection per transaction)
        "log_disconnections": "off",
        "log_timezone": "'UTC'",
        "timezone": "'UTC'",
    }
    lines = [
        "# Rendered by `playground render` from experiment.yaml + config/host.env.",
        "# Identical for both paths (control variable, Tabel 3.1). Do not edit by hand.",
        f"# postgres memory budget: {host['MEM_POSTGRES_MB']} MB",
    ]
    lines += [f"{k} = {v}" for k, v in settings.items()]
    return "\n".join(lines) + "\n"


def render_pg_hba(subnet: str) -> str:
    return (
        "# Rendered by `playground render`. Same auth method (scram-sha-256) for every\n"
        "# network client, so the direct path and PgBouncer authenticate the same way.\n"
        "local   all   all                 trust\n"
        "host    all   all   127.0.0.1/32  scram-sha-256\n"
        f"host    all   all   {subnet}  scram-sha-256\n"
    )


def render_pgbouncer_ini(
    cfg: dict[str, Any],
    host: dict[str, str],
    overrides: dict[str, Any],
    pg_host: str = "postgres",
    listen_addr: str = "*",
    logfile: str = "/var/log/pgbouncer/pgbouncer.log",
    auth_file: str = "/etc/pgbouncer/userlist.txt",
) -> str:
    pgb = cfg["pgbouncer"]
    p = pool_size(cfg, host, overrides)
    dbs = [cfg["datasets"][k]["database"] for k in ("campaign", "smoke")]
    db_lines = "\n".join(
        f"{db} = host={pg_host} port={cfg['postgres']['port']} dbname={db}" for db in dbs
    )
    return f"""; Rendered by `playground render` (Tabel 3.2). Do not edit by hand.
; default_pool_size rule: {pgb["pool_size_rule"]} -> {p}
;   physical cores of the PostgreSQL cpuset = {host["POSTGRES_PHYSICAL_CORES"]},
;   effective spindles = {pgb["effective_spindles"]}
;   calibration overrides = {json.dumps(overrides) if overrides else "none"}

[databases]
{db_lines}

[pgbouncer]
listen_addr = {listen_addr}
listen_port = {pgb["port"]}
auth_type = {pgb["auth_type"]}
auth_file = {auth_file}
admin_users = pgb_admin
stats_users = pgb_stats

pool_mode = {pgb["pool_mode"]}
default_pool_size = {p}
max_db_connections = {p}
min_pool_size = 0
reserve_pool_size = {pgb["reserve_pool_size"]}
max_client_conn = {_ov(overrides, "max_client_conn", pgb["max_client_conn"])}
query_wait_timeout = {_ov(overrides, "query_wait_timeout", pgb["query_wait_timeout"])}
max_prepared_statements = {pgb["max_prepared_statements"]}

; D10 / DEV-006: same order of listen backlog as PostgreSQL (2 x max_connections)
listen_backlog = {listen_backlog(cfg)}

; pgbouncer_exporter sends extra_float_digits as a startup parameter
ignore_startup_parameters = extra_float_digits

logfile = {logfile}
log_connections = 0
log_disconnections = 0
log_pooler_errors = 1
stats_period = 60
"""


def render_all(
    cfg: dict[str, Any],
    host: dict[str, str],
    out_dir: Path = ACTIVE_DIR,
    overrides: dict[str, Any] | None = None,
    subnet: str = "172.29.0.0/24",
    **ini_kwargs: Any,
) -> dict[str, Any]:
    overrides = load_overrides() if overrides is None else overrides
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "postgresql.conf").write_text(render_postgresql_conf(cfg, host), encoding="utf-8")
    (out_dir / "pg_hba.conf").write_text(render_pg_hba(subnet), encoding="utf-8")
    ini = render_pgbouncer_ini(cfg, host, overrides, **ini_kwargs)
    (out_dir / "pgbouncer.ini").write_text(ini, encoding="utf-8")
    summary = {
        "rule_pool_size": rule_pool_size(cfg, host),
        "default_pool_size": pool_size(cfg, host, overrides),
        "overrides": overrides,
        "postgres_memory": postgres_memory(cfg, host),
        "listen_backlog": listen_backlog(cfg),
        "host_env": host,
    }
    (out_dir / "RENDER.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
