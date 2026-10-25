"""DEV-019 memory check: classification logic (synthetic numbers only)."""

from __future__ import annotations

import pandas as pd

from playground.memcheck import container_max, overall, verdict


def test_verdict_safe_below_headroom():
    v, _ = verdict(3000, 1500, 1350, 5400, 0.85)
    assert v == "AMAN"


def test_verdict_safe_when_only_page_cache_is_over():
    # working set over 85 %, but RSS + shared_buffers well under it
    v, why = verdict(5000, 2000, 1350, 5400, 0.85)
    assert v == "AMAN" and "page cache" in why


def test_verdict_tight():
    v, _ = verdict(5300, 3800, 1350, 5400, 0.85)
    assert v == "MEPET"


def test_verdict_missing_data():
    v, _ = verdict(None, None, 0, 2048, 0.85)
    assert v == "TIDAK ADA DATA"


def test_container_max_filters_by_name():
    df = pd.DataFrame(
        {
            "series": ["mem_bytes_by_container"] * 3,
            "labels": [
                '{"name": "pgb-postgres"}',
                '{"name": "pgb-postgres"}',
                '{"name": "pgb-client"}',
            ],
            "ts": [1.0, 2.0, 1.0],
            "value": [100.0, 300.0, 999.0],
        }
    )
    assert container_max(df, "mem_bytes_by_container", "pgb-postgres") == 300.0
    assert container_max(None, "mem_bytes_by_container", "pgb-postgres") is None


def _rows(backends: int, restarted: bool = False, v: str = "AMAN"):
    return [
        {"path": "direct", "container": "postgres", "verdict": v, "reason": "x"},
        {
            "path": "direct",
            "container": "run",
            "verdict": "",
            "reason": "",
            "_res": {
                "pg_backends_max": backends,
                "aborted_clients": 0,
                "flag_container_restarted": restarted,
            },
        },
    ]


def test_overall_pass():
    ok, problems = overall(_rows(1003), 1000)
    assert ok and problems == []


def test_overall_fails_when_direct_does_not_hold_all_backends():
    ok, problems = overall(_rows(640), 1000)
    assert not ok and "backend" in problems[0]


def test_overall_fails_on_restart_or_tight_memory():
    assert not overall(_rows(1003, restarted=True), 1000)[0]
    assert not overall(_rows(1003, v="MEPET"), 1000)[0]
