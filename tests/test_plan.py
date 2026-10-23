from collections import Counter

import pytest

from playground.config import load_experiment
from playground.plan import make_plan, plan_from_config, read_plan, write_plan


@pytest.fixture(scope="module")
def plan():
    return plan_from_config(load_experiment(), smoke=False)


def test_plan_has_150_runs(plan):
    assert len(plan) == 150
    assert [r.seq for r in plan] == list(range(1, 151))


def test_every_cell_has_one_direct_and_one_pgbouncer_adjacent(plan):
    by_pair = {}
    for r in plan:
        by_pair.setdefault(r.pair_id, []).append(r)
    assert len(by_pair) == 75
    for rows in by_pair.values():
        assert sorted(x.path for x in rows) == ["direct", "pgbouncer"]
        assert abs(rows[0].seq - rows[1].seq) == 1  # back-to-back
        assert rows[0].pgbench_seed == rows[1].pgbench_seed  # same random keys


def test_each_round_contains_every_cell_once(plan):
    for rnd in range(1, 4):
        cells = Counter((r.profile, r.concurrency) for r in plan if r.round == rnd)
        assert len(cells) == 25 and set(cells.values()) == {2}


def test_path_order_is_randomized(plan):
    firsts = [r.path for i, r in enumerate(plan) if i % 2 == 0]
    counts = Counter(firsts)
    assert counts["direct"] > 20 and counts["pgbouncer"] > 20
    # the order changes between rounds for most cells (3 rounds: some cells keep it by chance)
    changed = 0
    for profile in ("R1", "R2", "R3", "R4", "R5"):
        for c in (1, 10, 100, 500, 1000):
            orders = {
                next(
                    r.path
                    for r in plan
                    if r.round == rnd and r.profile == profile and r.concurrency == c
                )
                for rnd in range(1, 4)
            }
            changed += orders == {"direct", "pgbouncer"}
    assert changed >= 10


def test_cells_keep_fixed_order_by_default(plan):
    """Proposal text: only path order is randomized (DEV-005 off)."""
    order = [(r.profile, r.concurrency) for r in plan if r.round == 1][::2]
    assert order == [
        (p, c) for p in ("R1", "R2", "R3", "R4", "R5") for c in (1, 10, 100, 500, 1000)
    ]


def test_shuffle_option_changes_cell_order():
    rows = make_plan(["R1", "R2"], [1, 2, 3], ["direct", "pgbouncer"], 3, 7, shuffle_cells=True)
    per_round = [
        tuple((r.profile, r.concurrency) for r in rows if r.round == k)[::2] for k in (1, 2, 3)
    ]
    assert len(set(per_round)) > 1


def test_plan_is_deterministic():
    a = make_plan(["R1"], [1, 2], ["direct", "pgbouncer"], 4, 123, False)
    b = make_plan(["R1"], [1, 2], ["direct", "pgbouncer"], 4, 123, False)
    assert a == b


def test_plan_roundtrip_and_tamper_detection(tmp_path, plan):
    path = tmp_path / "plan.csv"
    write_plan(plan, path)
    assert read_plan(path) == plan
    path.write_text(path.read_text().replace("pgbouncer", "direct", 1))
    with pytest.raises(RuntimeError, match="checksum"):
        read_plan(path)
