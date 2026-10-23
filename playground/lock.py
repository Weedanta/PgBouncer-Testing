"""Configuration lock (Subbab 3.3 "Penguncian Konfigurasi").

`make lock` copies the active configuration into config/locked/ together with the
workload hashes and the versions actually running, and records SHA-256 hashes of
everything in LOCK.json. From then on the runner refuses to start a campaign run if any
locked input changed, and checks that PgBouncer's live settings equal the locked file.
"""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from playground import db
from playground.config import REPO_ROOT
from playground.settings import Endpoints

LOCK_DIR = REPO_ROOT / "config" / "locked"
ACTIVE = REPO_ROOT / "config" / "active"
ACTIVE_FILES = ("postgresql.conf", "pg_hba.conf", "pgbouncer.ini")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def locked_inputs(repo: Path = REPO_ROOT) -> dict[str, str]:
    """Files whose content defines the treatment and the workload."""
    files = [repo / "experiment.yaml"]
    files += [repo / "config" / "active" / f for f in ACTIVE_FILES]
    files += sorted((repo / "workloads").rglob("*.sql"))
    overrides = repo / "config" / "calibration_overrides.yaml"
    if overrides.exists():
        files.append(overrides)
    return {str(f.relative_to(repo)): sha256(f) for f in files}


def combined(hashes: dict[str, str]) -> str:
    h = hashlib.sha256()
    for key in sorted(hashes):
        h.update(f"{key}:{hashes[key]}\n".encode())
    return h.hexdigest()


def ini_value(ini_text: str, key: str) -> str | None:
    for line in ini_text.splitlines():
        line = line.strip()
        if line.startswith(key) and "=" in line and line.split("=", 1)[0].strip() == key:
            return line.split("=", 1)[1].strip()
    return None


def runtime_versions(ep: Endpoints) -> dict[str, Any]:
    with db.superuser(ep) as conn:
        pg_version = db.rows(conn, "SELECT version() AS v")[0]["v"]
    with db.pgb_admin(ep) as admin:
        pgb_version = db.admin_rows(admin, "SHOW VERSION")
    return {
        "postgresql": pg_version,
        "pgbouncer": pgb_version[0] if pgb_version else None,
        "pgbench": db.pgbench_version(),
        "kernel": platform.release(),
        "python": platform.python_version(),
    }


def write_lock(ep: Endpoints, calibration_report: Path | None) -> dict[str, Any]:
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    for name in ACTIVE_FILES:
        shutil.copy2(ACTIVE / name, LOCK_DIR / name)
    wl = {k: v for k, v in locked_inputs().items() if k.startswith("workloads/")}
    (LOCK_DIR / "workloads.sha256").write_text(
        "".join(f"{v}  {k}\n" for k, v in sorted(wl.items())), encoding="utf-8"
    )
    versions = runtime_versions(ep)
    images = REPO_ROOT / "config" / "images.lock.json"
    if images.exists():
        versions["images"] = json.loads(images.read_text(encoding="utf-8"))
        shutil.copy2(images, LOCK_DIR / "images.lock.json")
    (LOCK_DIR / "versions.json").write_text(json.dumps(versions, indent=2), encoding="utf-8")
    inputs = locked_inputs()
    lock = {
        "locked_utc": datetime.now(UTC).isoformat(),
        "calibration_report": str(calibration_report) if calibration_report else None,
        "inputs": inputs,
        "inputs_sha256": combined(inputs),
        "locked_files": {
            p.name: sha256(p)
            for p in sorted(LOCK_DIR.iterdir())
            if p.is_file() and p.name != "LOCK.json"
        },
    }
    (LOCK_DIR / "LOCK.json").write_text(json.dumps(lock, indent=2), encoding="utf-8")
    return lock


def read_lock() -> dict[str, Any] | None:
    path = LOCK_DIR / "LOCK.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def verify(ep: Endpoints | None = None) -> list[str]:
    """Return problems; empty means the locked configuration is intact."""
    lock = read_lock()
    if lock is None:
        return ["config/locked/LOCK.json not found: run `make lock` after calibration"]
    problems = []
    current = locked_inputs()
    for key, digest in lock["inputs"].items():
        if current.get(key) != digest:
            problems.append(f"locked input changed: {key}")
    for key in set(current) - set(lock["inputs"]):
        problems.append(f"new input not in the lock: {key}")
    for name, digest in lock["locked_files"].items():
        path = LOCK_DIR / name
        if not path.exists() or sha256(path) != digest:
            problems.append(f"config/locked/{name} was modified")
    if ep is not None:
        ini = (LOCK_DIR / "pgbouncer.ini").read_text(encoding="utf-8")
        with db.pgb_admin(ep) as admin:
            live = {r["key"]: r["value"] for r in db.admin_rows(admin, "SHOW CONFIG")}
        for key in (
            "pool_mode",
            "default_pool_size",
            "max_db_connections",
            "reserve_pool_size",
            "max_client_conn",
            "query_wait_timeout",
            "max_prepared_statements",
        ):
            want = ini_value(ini, key)
            if want is not None and str(live.get(key)) != want:
                problems.append(f"PgBouncer live {key}={live.get(key)} differs from locked {want}")
    return problems
