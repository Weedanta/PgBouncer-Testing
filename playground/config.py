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
LAYOUTS = ("dedicated", "small_server")
PRIMARY_TESTS = ("factorial_anova_log",)
MIN_CONCURRENCY_LEVELS = 5  # supervisor, 20 Oct 2026: "paling tidak lima"


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


def profile_writes(cfg: dict[str, Any], profile: str) -> bool:
    return bool(cfg["profiles"][profile]["writes"])


def needs_reset(cfg: dict[str, Any], profile: str) -> bool:
    """Subbab 3.4: reset before every measurement unit of profiles that write data."""
    if cfg["reset"]["policy"] == "every_run":
        return True
    return profile_writes(cfg, profile)


def validate(cfg: dict[str, Any]) -> list[str]:
    """Return a list of human-readable problems; empty means valid."""
    errors: list[str] = []

    def need(cond: bool, msg: str) -> None:
        if not cond:
            errors.append(msg)

    need(cfg.get("schema_version") == 2, "schema_version must be 2")

    d = cfg["design"]
    need(tuple(d["paths"]) == PATHS, f"design.paths must be {list(PATHS)}")
    conc = d["concurrency"]
    need(
        bool(conc) and all(isinstance(c, int) and c > 0 for c in conc),
        "design.concurrency must be positive integers",
    )
    need(conc == sorted(set(conc)), "design.concurrency must be sorted and unique")
    need(
        len(conc) >= MIN_CONCURRENCY_LEVELS,
        f"design.concurrency needs at least {MIN_CONCURRENCY_LEVELS} levels to show a trend",
    )
    need(set(d["high_concurrency"]) <= set(conc), "design.high_concurrency must be ⊆ concurrency")
    need(tuple(d["profiles"]) == PROFILES, f"design.profiles must be {list(PROFILES)}")
    need(
        isinstance(d["rounds"], int) and d["rounds"] >= 2,
        "design.rounds must be >= 2 (the factorial model needs replications for its error term)",
    )
    need(d["warmup_s"] > 0 and d["measure_s"] > 0, "warmup_s and measure_s must be > 0")
    need(
        d["randomize_path_order"] is True, "design.randomize_path_order must stay true (Tabel 3.7)"
    )

    profiles = cfg["profiles"]
    need(set(profiles) == set(PROFILES), "profiles must define exactly R1..R5")
    for name, p in profiles.items():
        has_builtin, has_scripts = "builtin" in p, "scripts" in p
        need(has_builtin != has_scripts, f"{name}: define exactly one of builtin / scripts")
        for script, weight in (p.get("scripts") or {}).items():
            need(isinstance(weight, int) and weight > 0, f"{name}: weight of {script} must be > 0")
            need((REPO_ROOT / script).is_file(), f"{name}: workload file {script} is missing")
    r5 = {Path(k).name: v for k, v in (profiles.get("R5", {}).get("scripts") or {}).items()}
    need(r5 == R5_WEIGHTS, f"R5 scripts/weights must match Tabel 3.6: {R5_WEIGHTS}")
    need(sum(r5.values()) == 100, "R5 weights must sum to 100")
    need(profiles.get("R1", {}).get("builtin") == "select-only", "R1 is pgbench select-only")
    need(profiles.get("R3", {}).get("builtin") == "tpcb-like", "R3 is pgbench tpcb-like")
    need(profiles.get("R4", {}).get("builtin") == "tpcb-like", "R4 is R3 with --connect")
    need(profiles.get("R4", {}).get("connect_per_transaction") is True, "R4 must use -C")
    for name in ("R1", "R2", "R3", "R5"):
        need(
            profiles.get(name, {}).get("connect_per_transaction") is False,
            f"{name} uses persistent connections",
        )
    need(profiles.get("R1", {}).get("writes") is False, "R1 is read-only")

    top = max(conc) if conc else 0
    need(cfg["postgres"]["max_connections"] > top, "postgres.max_connections must exceed max c")
    pgb = cfg["pgbouncer"]
    need(pgb["pool_mode"] == "transaction", "pool_mode is fixed to transaction (Batasan 4)")
    need(pgb["max_client_conn"] > top, "pgbouncer.max_client_conn must exceed max c")
    need(pgb["reserve_pool_size"] == 0, "reserve_pool_size must be 0 (Tabel 3.2)")
    need(pgb["max_db_connections"] == "equal_to_pool", "max_db_connections = pool (Tabel 3.2)")
    need(pgb["effective_spindles"] >= 0, "effective_spindles must be >= 0")
    need(pgb["query_wait_timeout"] == 120, "query_wait_timeout stays at the default 120 s")
    need(pgb["max_prepared_statements"] == 200, "max_prepared_statements stays at 200")
    need(pgb["auth_type"] == "scram-sha-256", "auth_type must match pg_hba (scram-sha-256)")
    need(cfg["pgbench"]["protocol"] == "prepared", "pgbench -M prepared (Tabel 3.7)")
    thr = cfg["pgbench"]["threads"]
    need(
        thr in ("per_client", "cpuset") or (isinstance(thr, int) and thr > 0),
        "pgbench.threads must be per_client, cpuset or a positive integer",
    )

    res = cfg["resources"]
    need(res["layout"] in LAYOUTS, f"resources.layout must be one of {LAYOUTS}")
    need(res["pgbouncer_cores"] >= 1, "pgbouncer needs at least one core")
    need(0 < res["client_core_share"] < 1, "client_core_share must be in (0, 1)")
    small = res["small_server"]
    need(0 < small["pgbouncer_memory_mb"] < small["memory_mb"], "small_server memory must fit")

    for name, ds in cfg["datasets"].items():
        need(ds["scale"] > 0, f"datasets.{name}.scale must be > 0")
        need(0 < ds["r5_seed_fraction"] <= 1, f"datasets.{name}.r5_seed_fraction in (0, 1]")
        need(ds["database"] != ds["template"], f"datasets.{name}: database != template")
    need(
        cfg["datasets"]["campaign"]["database"] != cfg["datasets"]["smoke"]["database"],
        "smoke must use its own database",
    )
    need(-1 <= cfg["r5_seed"]["random_seed"] <= 1, "r5_seed.random_seed must be in [-1, 1]")

    need(cfg["reset"]["policy"] in RESET_POLICIES, f"reset.policy must be one of {RESET_POLICIES}")
    an = cfg["analysis"]
    need(
        an["primary_test"] in PRIMARY_TESTS, f"analysis.primary_test must be one of {PRIMARY_TESTS}"
    )
    budgets = an.get("memory", {}).get("budgets_mb", [])
    need(
        bool(budgets) and all(isinstance(b, int) and b > 0 for b in budgets),
        "analysis.memory.budgets_mb must be positive integers (MB)",
    )
    need(0 < cfg["analysis"]["alpha"] < 1, "analysis.alpha must be in (0, 1)")

    cal = cfg["calibration"]
    need(1 in cal["pool_size_factors"], "calibration.pool_size_factors must include 1 (P itself)")
    need(set(cal["pool_size_profiles"]) <= set(PROFILES), "calibration profiles must be R1..R5")
    need(
        set(cal["concurrency"]) <= set(conc) and max(conc) in cal["concurrency"],
        "calibration levels must be design levels and include the highest one",
    )

    smoke = cfg["smoke"]
    need(smoke["rounds"] >= 2, "smoke rounds must be >= 2 (factorial error term)")
    need(max(smoke["concurrency"]) < top, "smoke concurrency must stay below the real levels")
    return errors
