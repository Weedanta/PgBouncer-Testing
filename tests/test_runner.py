import pytest

from playground.config import load_experiment
from playground.runner import (
    ATTEMPT_FIELDS,
    append_row,
    compute_flags,
    read_csv,
    verify_append_only,
)


def test_append_only_with_checksum(tmp_path):
    path = tmp_path / "attempts.csv"
    append_row(path, ATTEMPT_FIELDS, {"seq": 1, "run_id": "a", "attempt": 1, "outcome": "ok"})
    append_row(path, ATTEMPT_FIELDS, {"seq": 2, "run_id": "b", "attempt": 1, "outcome": "ok"})
    assert [r["run_id"] for r in read_csv(path)] == ["a", "b"]
    verify_append_only(path)
    path.write_text(path.read_text().replace("ok", "infra_error", 1))
    with pytest.raises(RuntimeError, match="checksum"):
        verify_append_only(path)


def base_result(**kw):
    r = {
        "client_cpu_mean": None,
        "pgbouncer_cpu_mean": None,
        "aborted_clients": 0,
        "failed_tx": 0,
        "checkpoints_during_run": 0,
        "tx_processed": 100,
        "n_ok": 100,
    }
    r.update(kw)
    return r


def test_flags_are_computed_not_applied(monkeypatch):
    cfg = load_experiment()
    monkeypatch.setenv("PLAYGROUND_CPUSET_CLIENT", "3,11,4,12")
    f = compute_flags(
        cfg,
        base_result(
            client_cpu_mean=3.9,
            pgbouncer_cpu_mean=0.5,
            checkpoints_during_run=1,
            n_ok=99,
            failed_tx=2,
        ),
        {"postmaster_restarted": False},
        {"prometheus": True, "r5_invariants": [{"ok": False}]},
    )
    assert f["flag_client_cpu_saturated"] is True  # 3.9 >= 0.95 * 4
    assert f["flag_pgbouncer_cpu_saturated"] is False
    assert f["flag_checkpoint_during_run"] is True
    assert f["flag_log_count_mismatch"] is True
    assert f["flag_failed_tx"] is True
    assert f["flag_invariant_failed"] is True
    assert f["flag_prometheus_missing"] is False


def test_unknown_cpu_gives_none_not_false():
    cfg = load_experiment()
    f = compute_flags(cfg, base_result(), {}, {"prometheus": False})
    assert f["flag_client_cpu_saturated"] is None
    assert f["flag_prometheus_missing"] is True
