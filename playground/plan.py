"""Campaign plan (Tabel 3.7 v5): 2 paths x 5 profiles x 5 concurrency levels x 3 rounds.

The whole plan is written up front from the master seed, so the run order is fixed and
auditable before the first run starts.
- Every round contains every (profile, concurrency) cell once.
- Inside a cell the two paths run back-to-back (a pair) in random order; the path order
  is drawn again in every round (Tabel 3.7 "urutan jalur diacak di setiap putaran").
- Cells keep a fixed order unless design.shuffle_cells_within_round is on (DEV-005).
- Both runs of a pair share one pgbench --random-seed, so they see the same random keys.
"""

from __future__ import annotations

import csv
import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

PLAN_FIELDS = ["seq", "round", "profile", "concurrency", "path", "pair_id", "pgbench_seed"]


@dataclass(frozen=True)
class PlanRow:
    seq: int
    round: int
    profile: str
    concurrency: int
    path: str
    pair_id: str
    pgbench_seed: int

    @property
    def run_id(self) -> str:
        return f"{self.seq:03d}_r{self.round:02d}_{self.profile}_c{self.concurrency}_{self.path}"


def make_plan(
    profiles: list[str],
    concurrency: list[int],
    paths: list[str],
    rounds: int,
    master_seed: int,
    shuffle_cells: bool,
) -> list[PlanRow]:
    rng = np.random.default_rng(master_seed)
    rows: list[PlanRow] = []
    seq = 1
    cells = [(p, c) for p in profiles for c in concurrency]
    for rnd in range(1, rounds + 1):
        order = list(cells)
        if shuffle_cells:
            order = [order[i] for i in rng.permutation(len(order))]
        for profile, conc in order:
            path_order = [paths[i] for i in rng.permutation(len(paths))]
            seed = int(rng.integers(1, 2**31 - 1))
            pair_id = f"r{rnd:02d}-{profile}-c{conc}"
            for path in path_order:
                rows.append(PlanRow(seq, rnd, profile, conc, path, pair_id, seed))
                seq += 1
    return rows


def plan_from_config(cfg: dict[str, Any], smoke: bool) -> list[PlanRow]:
    d = cfg["design"]
    return make_plan(
        profiles=list(d["profiles"]),
        concurrency=list(cfg["smoke"]["concurrency"] if smoke else d["concurrency"]),
        paths=list(d["paths"]),
        rounds=cfg["smoke"]["rounds"] if smoke else d["rounds"],
        master_seed=cfg["seeds"]["master"],
        shuffle_cells=d["shuffle_cells_within_round"],
    )


def write_plan(rows: list[PlanRow], path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=PLAN_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_suffix(".csv.sha256").write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    return digest


def read_plan(path: Path) -> list[PlanRow]:
    digest_file = path.with_suffix(".csv.sha256")
    if digest_file.exists():
        expected = digest_file.read_text().split()[0]
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if expected != actual:
            raise RuntimeError(f"{path} was modified after it was written (checksum mismatch)")
    with open(path, newline="", encoding="utf-8") as fh:
        return [
            PlanRow(
                int(r["seq"]),
                int(r["round"]),
                r["profile"],
                int(r["concurrency"]),
                r["path"],
                r["pair_id"],
                int(r["pgbench_seed"]),
            )
            for r in csv.DictReader(fh)
        ]
