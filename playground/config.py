"""Load and validate experiment.yaml, the single source of truth for the design."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EXPERIMENT = REPO_ROOT / "experiment.yaml"

PATHS = ("direct", "pgbouncer")
PROFILES = ("R1", "R2", "R3", "R4", "R5")
# Tabel 3.6 of the proposal: file names and weights are part of the locked design.
R5_WEIGHTS = {
    "autosave.sql": 35,
    "ambil_soal.sql": 25,
    "heartbeat.sql": 15,
    "login.sql": 10,
    "leaderboard.sql": 5,
    "daftar.sql": 4,
    "submit.sql": 4,
    "nomor_peserta.sql": 2,
}
RESET_POLICIES = ("every_run", "write_profiles_only")
EFFECT_SIZE_N = ("pairs", "observations")


class ConfigError(ValueError):
    """experiment.yaml violates the research design."""


def results_dir() -> Path:
    """Root for measurement output. Overridable so data can live outside the repo."""
    return Path(os.environ.get("PLAYGROUND_RESULTS_DIR", REPO_ROOT / "results"))


def load_experiment(path: Path | str = DEFAULT_EXPERIMENT) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    errors = validate(cfg)
    if errors:
        raise ConfigError("experiment.yaml is invalid:\n  - " + "\n  - ".join(errors))
    return cfg


def measured_runs(cfg: dict[str, Any]) -> int:
    d = cfg["design"]
    return len(d["paths"]) * len(d["profiles"]) * len(d["concurrency"]) * d["rounds"]


def validate(cfg: dict[str, Any]) -> list[str]:
    """Return a list of human-readable problems; empty means valid."""
    errors: list[str] = []

    def need(cond: bool, msg: str) -> None:
        if not cond:
            errors.append(msg)

    need(cfg.get("schema_version") == 1, "schema_version must be 1")

    d = cfg["design"]
    need(tuple(d["paths"]) == PATHS, f"design.paths must be {list(PATHS)}")
    conc = d["concurrency"]
    need(
        bool(conc) and all(isinstance(c, int) and c > 0 for c in conc),
        "design.concurrency must be positive integers",
    )
    need(conc == sorted(set(conc)), "design.concurrency must be sorted and unique")
    need(set(d["high_concurrency"]) <= set(conc), "design.high_concurrency must be ⊆ concurrency")
    need(tuple(d["profiles"]) == PROFILES, f"design.profiles must be {list(PROFILES)}")
    need(isinstance(d["rounds"], int) and d["rounds"] >= 1, "design.rounds must be >= 1")
    need(d["warmup_s"] > 0 and d["measure_s"] > 0, "warmup_s and measure_s must be > 0")

    profiles = cfg["profiles"]
    need(set(profiles) == set(PROFILES), "profiles must define exactly R1..R5")
    for name, p in profiles.items():
        has_builtin, has_scripts = "builtin" in p, "scripts" in p
        need(has_builtin != has_scripts, f"{name}: define exactly one of builtin / scripts")
        for script, weight in (p.get("scripts") or {}).items():
            need(isinstance(weight, int) and weight > 0, f"{name}: weight of {script} must be > 0")
    r5 = {Path(k).name: v for k, v in (profiles.get("R5", {}).get("scripts") or {}).items()}
    need(r5 == R5_WEIGHTS, f"R5 scripts/weights must match Tabel 3.6: {R5_WEIGHTS}")
    need(sum(r5.values()) == 100, "R5 weights must sum to 100")
    need(profiles.get("R4", {}).get("connect_per_transaction") is True, "R4 must use -C")

    top = max(conc) if conc else 0
    need(cfg["postgres"]["max_connections"] > top, "postgres.max_connections must exceed max c")
    pgb = cfg["pgbouncer"]
    need(pgb["pool_mode"] == "transaction", "pool_mode is fixed to transaction (non-goal: others)")
    need(pgb["max_client_conn"] > top, "pgbouncer.max_client_conn must exceed max c")
    need(pgb["reserve_pool_size"] == 0, "reserve_pool_size must be 0")
    need(pgb["effective_spindles"] >= 0, "effective_spindles must be >= 0")

    srv = cfg["server"]
    need(srv["vcpus"] >= 1, "server.vcpus must be >= 1")
    need(0 < srv["pgbouncer_memory_mb"] < srv["memory_mb"], "pgbouncer memory must fit the server")

    need(cfg["reset"]["policy"] in RESET_POLICIES, f"reset.policy must be one of {RESET_POLICIES}")
    need(
        cfg["analysis"]["effect_size_n"] in EFFECT_SIZE_N,
        f"analysis.effect_size_n must be one of {EFFECT_SIZE_N}",
    )
    need(0 < cfg["analysis"]["alpha"] < 1, "analysis.alpha must be in (0, 1)")

    cal = cfg["calibration"]
    need(1 in cal["pool_size_factors"], "calibration.pool_size_factors must include 1 (P itself)")
    need(set(cal["pool_size_profiles"]) <= set(PROFILES), "calibration profiles must be R1..R5")

    smoke = cfg["smoke"]
    need(smoke["scale"] > 0 and smoke["rounds"] >= 1, "smoke scale and rounds must be positive")
    need(max(smoke["concurrency"]) < top, "smoke concurrency must stay below the real levels")
    return errors
