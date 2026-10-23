"""Where things are at runtime. Defaults match docker-compose.yml (client container).

Every value can be overridden with an environment variable, which is how the native
development stack (PostgreSQL + PgBouncer on localhost) is tested.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from playground.config import REPO_ROOT


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Endpoints:
    pg_host: str
    pg_port: int
    pgb_host: str
    pgb_port: int
    prometheus_url: str
    grafana_url: str
    pgbouncer_log: Path
    secrets_dir: Path

    def host_port(self, path: str) -> tuple[str, int]:
        if path == "direct":
            return self.pg_host, self.pg_port
        if path == "pgbouncer":
            return self.pgb_host, self.pgb_port
        raise ValueError(path)


def endpoints() -> Endpoints:
    return Endpoints(
        pg_host=_env("PLAYGROUND_PG_HOST", "postgres"),
        pg_port=int(_env("PLAYGROUND_PG_PORT", "5432")),
        pgb_host=_env("PLAYGROUND_PGB_HOST", "pgbouncer"),
        pgb_port=int(_env("PLAYGROUND_PGB_PORT", "6432")),
        prometheus_url=_env("PLAYGROUND_PROMETHEUS_URL", "http://prometheus:9090"),
        grafana_url=_env("PLAYGROUND_GRAFANA_URL", "http://grafana:3000"),
        pgbouncer_log=Path(_env("PLAYGROUND_PGBOUNCER_LOG", "/var/log/pgbouncer/pgbouncer.log")),
        secrets_dir=Path(_env("PLAYGROUND_SECRETS_DIR", str(REPO_ROOT / "secrets"))),
    )


def prometheus_required() -> bool:
    """Campaign runs need Prometheus. Only the native dev stack may run without it."""
    return _env("PLAYGROUND_ALLOW_NO_PROMETHEUS", "0") != "1"


def cpuset_size(var: str) -> int | None:
    """Number of logical CPUs in a cpuset string such as '2,3,10-11'."""
    spec = os.environ.get(var, "")
    if not spec:
        return None
    n = 0
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-")
            n += int(b) - int(a) + 1
        elif part.strip():
            n += 1
    return n


def pgbench_threads(policy: str | int, clients: int) -> int:
    """-j for one run: one thread per client (DEV-013), the client cpuset, or a number."""
    if policy == "per_client":
        return clients
    if policy == "cpuset":
        return max(1, min(client_threads(), clients))
    return max(1, min(int(policy), clients))


def client_threads() -> int:
    """pgbench -j: logical CPUs available to this process (the client cpuset)."""
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:  # pragma: no cover - non-Linux
        return os.cpu_count() or 1
