"""Selection rule of the pool sweep (DEV-022), on SYNTHETIC medians only."""

from playground.poolsweep import candidate_verdict

CELLS = [("R3", 100), ("R5", 1000)]


def _m(values):
    return {
        (prof, c, s): {"tps": t, "p95_ms": p}
        for (prof, c), row in zip(CELLS, values, strict=True)
        for s, (t, p) in row.items()
    }


def test_candidate_rejected_when_double_is_clearly_better():
    med = _m([{6: (1000, 80), 12: (1500, 50), 24: (2100, 35)}] * 2)
    v = candidate_verdict(med, CELLS, 6, 12, 24, 0.95, 1.10)
    assert not v["passed"]


def test_candidate_accepted_when_gain_flattens():
    med = _m([{12: (1500, 50), 24: (2100, 35), 48: (2150, 34)}] * 2)
    v = candidate_verdict(med, CELLS, 12, 24, 48, 0.95, 1.10)
    assert v["passed"]
    assert all(abs(r["tps_ratio"] - 2100 / 2150) < 1e-9 for r in v["rows"])


def test_every_cell_must_pass():
    med = _m(
        [
            {12: (1500, 50), 24: (2100, 35), 48: (2150, 34)},
            {12: (1500, 50), 24: (2100, 35), 48: (2600, 30)},
        ]
    )
    assert not candidate_verdict(med, CELLS, 12, 24, 48, 0.95, 1.10)["passed"]
