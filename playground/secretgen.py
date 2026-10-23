"""Generate local secrets into secrets/ (gitignored). Never commit them.

Files are world-readable (0644) on purpose: the postgres (uid 999) and pgbouncer
processes inside the containers must read the bind-mounted files. The directory is
local to the test host and ignored by git.
"""

from __future__ import annotations

import secrets
from pathlib import Path

from playground.config import REPO_ROOT

SECRETS_DIR = REPO_ROOT / "secrets"
NAMES = (
    "postgres_password",
    "bench_password",
    "exporter_password",
    "pgb_admin_password",
    "pgb_stats_password",
    "grafana_admin_password",
)


def generate(force: bool = False, directory: Path = SECRETS_DIR) -> list[str]:
    directory.mkdir(parents=True, exist_ok=True)
    created = []
    values: dict[str, str] = {}
    for name in NAMES:
        path = directory / name
        if path.exists() and not force:
            values[name] = path.read_text().strip()
            continue
        values[name] = secrets.token_urlsafe(24)
        path.write_text(values[name] + "\n")
        path.chmod(0o644)
        created.append(name)
    # PgBouncer auth_file. Plain-text passwords let PgBouncer do SCRAM with the client
    # and with PostgreSQL (auth_type = scram-sha-256 on both sides).
    userlist = directory / "userlist.txt"
    userlist.write_text(
        f'"bench" "{values["bench_password"]}"\n'
        f'"pgb_admin" "{values["pgb_admin_password"]}"\n'
        f'"pgb_stats" "{values["pgb_stats_password"]}"\n'
    )
    userlist.chmod(0o644)
    # For docker compose interpolation (pgbouncer_exporter connection string).
    env = directory / "secrets.env"
    env.write_text(f"PGB_STATS_PASSWORD={values['pgb_stats_password']}\n")
    env.chmod(0o644)
    return created


def read_secret(name: str, directory: Path | None = None) -> str:
    """Read a secret from /run/secrets (inside containers) or secrets/ (host)."""
    candidates = [Path("/run/secrets") / name, (directory or SECRETS_DIR) / name]
    for path in candidates:
        if path.exists():
            return path.read_text().strip()
    raise FileNotFoundError(f"secret {name} not found (run `make secrets`)")
