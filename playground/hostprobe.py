"""Inspect the Linux host and turn the resource policy into concrete cpusets.

Runs on the host (WSL2 Ubuntu or bare Linux), not in a container. Writes
`config/host.env` (read by docker compose and by `playground render`) and
`docs/HOST.md` (human-readable host specification for the thesis appendix).

Rules (Subbab 3.4 and experiment.yaml `resources`):
- hyperthread siblings of one physical core always stay in the same container,
- one or more physical cores are left to the host OS / Docker daemon,
- monitoring gets its own core(s); PgBouncer gets one physical core (single process),
- the client (pgbench) gets `client_core_share` of the remaining cores, PostgreSQL the rest.
"""

from __future__ import annotations

import math
import os
import platform
import shutil
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass
class Core:
    socket: int
    core: int
    cpus: list[int]


def _run(cmd: list[str]) -> str:
    if not shutil.which(cmd[0]):
        return ""
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return proc.stdout.strip()


def read_topology(lscpu_e: str | None = None) -> list[Core]:
    """Physical cores with their logical CPUs, from `lscpu -e` (fallback: sysfs)."""
    text = lscpu_e if lscpu_e is not None else _run(["lscpu", "-e=CPU,CORE,SOCKET,ONLINE"])
    groups: dict[tuple[int, int], list[int]] = defaultdict(list)
    if text:
        for line in text.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 4 or parts[3].lower() != "yes":
                continue
            cpu, core, sock = int(parts[0]), int(parts[1]), int(parts[2])
            groups[(sock, core)].append(cpu)
    else:
        for cpu_dir in sorted(Path("/sys/devices/system/cpu").glob("cpu[0-9]*")):
            topo = cpu_dir / "topology"
            if not topo.exists():
                continue
            cpu = int(cpu_dir.name[3:])
            core = int((topo / "core_id").read_text())
            sock = int((topo / "physical_package_id").read_text())
            groups[(sock, core)].append(cpu)
    return [Core(s, c, sorted(cpus)) for (s, c), cpus in sorted(groups.items())]


def meminfo_mb() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemTotal:"):
            return int(line.split()[1]) // 1024
    raise RuntimeError("MemTotal not found")


def cpuset(cores: list[Core]) -> str:
    return ",".join(str(c) for core in cores for c in core.cpus)


def plan_resources(cores: list[Core], mem_total_mb: int, res: dict[str, Any]) -> dict[str, Any]:
    """Split cores and memory between containers. Raises ValueError if it cannot fit."""
    take = list(cores)

    def grab(n: int, what: str) -> list[Core]:
        if n > len(take):
            raise ValueError(f"not enough physical cores for {what} (need {n}, left {len(take)})")
        got = take[:n]
        del take[:n]
        return got

    host = grab(res["host_reserved_cores"], "host")
    mon = grab(res["monitoring_cores"], "monitoring")
    mem = res["memory_mb"]
    if res["layout"] == "dedicated":
        pgb = grab(res["pgbouncer_cores"], "pgbouncer")
        if len(take) < 2:
            raise ValueError("need at least 2 physical cores left for client and postgres")
        n_client = max(1, math.ceil(res["client_core_share"] * len(take)))
        n_client = min(n_client, len(take) - 1)
        client = grab(n_client, "client")
        postgres = grab(len(take), "postgres")
        mem_pg = (
            mem_total_mb
            - mem["host_reserved"]
            - mem["monitoring"]
            - mem["client"]
            - mem["pgbouncer"]
        )
        mem_pgb = mem["pgbouncer"]
        cpus_pgb = cpuset(pgb)
    else:  # small_server: PostgreSQL + PgBouncer share one emulated 2 vCPU server
        small = res["small_server"]
        server_cores = max(1, small["vcpus"] // 2)
        postgres = grab(server_cores, "server")
        client = grab(len(take), "client")
        mem_pg = small["memory_mb"] - small["pgbouncer_memory_mb"]
        mem_pgb = small["pgbouncer_memory_mb"]
        cpus_pgb = cpuset(postgres)
    if mem_pg < 2048:
        raise ValueError(f"postgres would get only {mem_pg} MB; need at least 2048 MB")
    return {
        "LAYOUT": res["layout"],
        "CPUSET_HOST": cpuset(host),
        "CPUSET_MONITOR": cpuset(mon),
        "CPUSET_PGBOUNCER": cpus_pgb,
        "CPUSET_POSTGRES": cpuset(postgres),
        "CPUSET_CLIENT": cpuset(client),
        "POSTGRES_PHYSICAL_CORES": len(postgres),
        "CLIENT_LOGICAL_CPUS": sum(len(c.cpus) for c in client),
        "MEM_POSTGRES_MB": mem_pg,
        "MEM_PGBOUNCER_MB": mem_pgb,
        "MEM_CLIENT_MB": mem["client"],
    }


def host_facts() -> dict[str, str]:
    def read(path: str) -> str:
        try:
            return Path(path).read_text().strip()
        except OSError:
            return "n/a"

    model = ""
    for line in read("/proc/cpuinfo").splitlines():
        if line.startswith("model name"):
            model = line.split(":", 1)[1].strip()
            break
    return {
        "kernel": platform.release(),
        "os": _run(["sh", "-c", ". /etc/os-release && echo $PRETTY_NAME"]) or platform.platform(),
        "wsl": "yes" if "microsoft" in platform.release().lower() else "no",
        "cpu_model": model,
        "logical_cpus": str(os.cpu_count()),
        "governor": read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"),
        "thp": read("/sys/kernel/mm/transparent_hugepage/enabled"),
        "swappiness": read("/proc/sys/vm/swappiness"),
        "swap": _run(["sh", "-c", "swapon --show=NAME,SIZE --noheadings | tr '\\n' ' '"]) or "none",
        "disks": _run(["lsblk", "-d", "-o", "NAME,MODEL,SIZE,ROTA,TYPE"]),
        "docker": _run(["docker", "version", "--format", "{{.Server.Version}}"]) or "n/a",
        "compose": _run(["docker", "compose", "version", "--short"]) or "n/a",
    }


# Rough on-disk size of the campaign dataset: pgbench is about 16 MB per scale unit
# (tables + indexes); the R5 schema at full seed is about 600 MB. Only used for a warning;
# the real sizes are measured by `make init-data` (results/datasets/).
PGBENCH_MB_PER_SCALE = 16
R5_FULL_SEED_MB = 600


def dataset_estimate_mb(cfg: dict[str, Any]) -> int:
    ds = cfg["datasets"]["campaign"]
    return int(ds["scale"] * PGBENCH_MB_PER_SCALE + R5_FULL_SEED_MB * ds["r5_seed_fraction"])


def warnings(
    cores: list[Core], mem_total_mb: int, cfg: dict[str, Any], mem_postgres_mb: int | None = None
) -> list[str]:
    out = []
    if len(cores) < 8:
        out.append(f"only {len(cores)} physical cores (recommended: 8 or more)")
    if mem_total_mb < 30000:
        out.append(f"only {mem_total_mb} MB RAM visible (recommended: 32 GB)")
    # Subbab 3.5.2 (v4): the dataset must fit in memory (also the basis of
    # effective_spindles = 0 in the pool-size rule).
    est = dataset_estimate_mb(cfg)
    budget = mem_postgres_mb if mem_postgres_mb is not None else mem_total_mb
    cache = budget * cfg["postgres"]["effective_cache_size_fraction"]
    if est > cache:
        out.append(
            f"dataset (~{est} MB at scale {cfg['datasets']['campaign']['scale']}) is larger "
            f"than the ~{cache:.0f} MB the postgres container can cache: it will not stay in "
            "memory, contrary to Subbab 3.5.2 (effective_spindles = 0)"
        )
    return out


def host_md(
    plan: dict[str, Any],
    facts: dict[str, str],
    cores: list[Core],
    mem_total_mb: int,
    warn: list[str],
) -> str:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    rows = "\n".join(f"| {k} | {v} |" for k, v in facts.items() if k != "disks")
    lines = [
        "# Host specification",
        "",
        f"> Generated by `make probe` on {now}. Do not edit by hand; rerun the probe.",
        "",
        "## Machine",
        "",
        "| Item | Value |",
        "|---|---|",
        rows,
        f"| physical cores | {len(cores)} |",
        f"| memory visible to Linux | {mem_total_mb} MB |",
        "",
        "Disks:",
        "",
        "```",
        facts.get("disks", ""),
        "```",
        "",
        f"## Resource split (layout: `{plan['LAYOUT']}`)",
        "",
        "| Container group | Logical CPUs (cpuset) | Memory |",
        "|---|---|---|",
        f"| Host OS + Docker (left free) | {plan['CPUSET_HOST']} | - |",
        f"| Monitoring | {plan['CPUSET_MONITOR']} | per-container limits in compose |",
        f"| PgBouncer | {plan['CPUSET_PGBOUNCER']} | {plan['MEM_PGBOUNCER_MB']} MB |",
        f"| PostgreSQL | {plan['CPUSET_POSTGRES']} | {plan['MEM_POSTGRES_MB']} MB |",
        f"| Client (pgbench + runner) | {plan['CPUSET_CLIENT']} | {plan['MEM_CLIENT_MB']} MB |",
        "",
        f"Physical cores of PostgreSQL (pool-size rule input, D1): "
        f"**{plan['POSTGRES_PHYSICAL_CORES']}**.",
        "",
    ]
    if warn:
        lines += ["## Warnings", ""] + [f"- {w}" for w in warn] + [""]
    return "\n".join(lines)
