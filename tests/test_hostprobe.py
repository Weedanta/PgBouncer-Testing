"""Host probe: resource split and the dataset-in-memory warning (Subbab 3.5.2, v4)."""

from __future__ import annotations

import copy

from playground.config import load_experiment
from playground.hostprobe import Core, dataset_estimate_mb, plan_resources, warnings

# Weda's laptop: Ryzen 7 6800H, 8 physical cores / 16 threads, WSL2 with 13 GB.
LAPTOP = [Core(socket=0, core=i, cpus=[2 * i, 2 * i + 1]) for i in range(8)]
WSL_MB = 13 * 1024


def _dataset_warnings(cfg: dict, mem_pg: int) -> list[str]:
    return [w for w in warnings(LAPTOP, WSL_MB, cfg, mem_postgres_mb=mem_pg) if "dataset" in w]


def test_laptop_split_gives_pool_six():
    cfg = load_experiment()
    plan = plan_resources(LAPTOP, WSL_MB, cfg["resources"])
    assert plan["POSTGRES_PHYSICAL_CORES"] == 3  # 2 x 3 + 0 = 6
    assert plan["MEM_POSTGRES_MB"] > 4000


def test_scale_100_fits_in_memory_on_the_laptop():
    cfg = load_experiment()
    plan = plan_resources(LAPTOP, WSL_MB, cfg["resources"])
    assert dataset_estimate_mb(cfg) < 2500
    assert _dataset_warnings(cfg, plan["MEM_POSTGRES_MB"]) == []


def test_scale_1000_is_flagged_as_not_in_memory():
    cfg = copy.deepcopy(load_experiment())
    cfg["datasets"]["campaign"]["scale"] = 1000
    plan = plan_resources(LAPTOP, WSL_MB, cfg["resources"])
    warn = _dataset_warnings(cfg, plan["MEM_POSTGRES_MB"])
    assert len(warn) == 1 and "Subbab 3.5.2" in warn[0]
