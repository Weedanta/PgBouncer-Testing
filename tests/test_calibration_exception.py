"""Check-4 exception for the lock (DEV-024), on SYNTHETIC calibration reports only."""

import json

import pytest

from playground.calibrate import check4_exception


def _report(tmp_path, name="20990101T000000Z", check3=True, quick=False):
    d = tmp_path / name
    d.mkdir()
    r = {
        "quick": quick,
        "passed": False,
        "step_a": {"lint_passed": True, "pool_size_under_test": 24},
        "check1_error_free": {"passed": True},
        "check2_rotation": {"passed": True},
        "check3_limits": {"passed": check3},
        "live_pool_restored": True,
        "check4_pool_size": {
            "verdicts": [
                {
                    "profile": "R3",
                    "concurrency": 100,
                    "tps_ratio": 0.9,
                    "p95_ratio": 1.0,
                    "passed": False,
                },
                {
                    "profile": "R5",
                    "concurrency": 100,
                    "tps_ratio": 1.0,
                    "p95_ratio": 1.0,
                    "passed": True,
                },
            ]
        },
    }
    (d / "REPORT.json").write_text(json.dumps(r), encoding="utf-8")
    return d


def test_only_check4_failed_is_accepted(tmp_path):
    e = check4_exception(_report(tmp_path), "synthetic reason")
    assert e["pool_size"] == 24
    assert e["reason"] == "synthetic reason"
    assert e["failed_check4_cells"] == [
        {"profile": "R3", "concurrency": 100, "tps_ratio": 0.9, "p95_ratio": 1.0}
    ]


def test_other_failed_check_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="check3_limits"):
        check4_exception(_report(tmp_path, check3=False), "synthetic reason")


def test_reason_is_required(tmp_path):
    with pytest.raises(ValueError, match="reason"):
        check4_exception(_report(tmp_path), "  ")


@pytest.mark.parametrize(
    "name,quick", [("20990101T000000Z-SMOKE", False), ("20990101T000000Z", True)]
)
def test_smoke_or_quick_calibration_is_rejected(tmp_path, name, quick):
    with pytest.raises(ValueError):
        check4_exception(_report(tmp_path, name=name, quick=quick), "synthetic reason")
