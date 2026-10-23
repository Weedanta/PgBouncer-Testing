"""pgbench command building and parsing. All log data here is SYNTHETIC."""

import numpy as np
import pytest

from playground.config import REPO_ROOT, load_experiment
from playground.pgbench import (
    PgbenchSpec,
    build_command,
    parse_logs,
    parse_summary,
    percentile_from_hist,
    r5_vars,
)
from playground.settings import pgbench_threads


@pytest.fixture(scope="module")
def cfg():
    return load_experiment()


def spec(profile, c=100, **kw):
    base = dict(
        profile=profile,
        path="pgbouncer",
        host="pgbouncer",
        port=6432,
        dbname="bench",
        clients=c,
        threads=pgbench_threads("per_client", c),
        duration_s=300,
        seed=7,
        scale=1000,
    )
    base.update(kw)
    return PgbenchSpec(**base)


def test_common_flags(cfg):
    cmd = build_command(cfg, spec("R1"))
    joined = " ".join(cmd)
    for part in (
        "-M prepared",
        "-c 100",
        "-j 100",
        "-T 300",
        "--random-seed=7",
        "--max-tries=1",
        "--failures-detailed",
        "-n",
        "-b select-only",
    ):
        assert part in joined
    assert cmd[-1] == "bench"
    assert "-C" not in cmd


def test_r4_reconnects_per_transaction(cfg):
    cmd = build_command(cfg, spec("R4"))
    assert "-C" in cmd and "tpcb-like" in cmd


def test_r2_passes_scale_for_custom_script(cfg):
    cmd = build_command(cfg, spec("R2"))
    assert cmd[cmd.index("-s") + 1] == "1000"
    assert any(a.replace("\\", "/").endswith("workloads/r2_write.sql@1") for a in cmd)


def test_r5_scripts_weights_and_vars(cfg):
    cmd = build_command(cfg, spec("R5", r5_vars=r5_vars(cfg, "campaign")))
    files = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-f"]
    weights = {
        f.replace("\\", "/").rsplit("/", 1)[1].split("@")[0]: int(f.rsplit("@", 1)[1])
        for f in files
    }
    assert weights["autosave.sql"] == 35 and sum(weights.values()) == 100
    assert "peserta=200000" in cmd and "kompetisi=20" in cmd and "soal=50" in cmd


def test_log_prefix_only_when_measuring(cfg, tmp_path):
    assert "--log" not in build_command(cfg, spec("R1"))
    cmd = build_command(cfg, spec("R1", log_prefix=tmp_path / "pgbench_log"))
    assert "--log" in cmd


def test_thread_policies():
    assert pgbench_threads("per_client", 1000) == 1000
    assert pgbench_threads(4, 2) == 2
    assert pgbench_threads("cpuset", 1000) >= 1


SUMMARY = """transaction type: <builtin: TPC-B (sort of)>
scaling factor: 10
query mode: prepared
number of clients: 10
number of threads: 10
maximum number of tries: 1
duration: 5 s
number of transactions actually processed: 9812
number of failed transactions: 3 (0.031%)
number of serialization failures: 0 (0.000%)
number of deadlock failures: 3 (0.031%)
latency average = 5.012 ms
latency stddev = 2.100 ms
initial connection time = 12.345 ms
tps = 1989.291642 (without initial connection time)
"""


def test_parse_summary():
    s = parse_summary(SUMMARY, "pgbench: error: client 3 aborted in command 2\n")
    assert s["tx_processed"] == 9812 and s["failed_tx"] == 3 and s["deadlock_failures"] == 3
    assert s["tps_summary"] == pytest.approx(1989.291642)
    assert s["latency_avg_ms"] == pytest.approx(5.012)
    assert s["initial_conn_ms"] == pytest.approx(12.345)
    assert s["aborted_clients"] == 1
    assert s["tps_summary_note"] == "without initial connection time"


def test_parse_summary_connect_mode_and_limits():
    s = parse_summary(
        "average connection time = 3.5 ms\ntps = 10.0 (including reconnection times)\n",
        "FATAL:  query_wait_timeout\nno more connections allowed\n",
    )
    assert s["avg_conn_ms"] == 3.5
    assert s["limit_errors"]["query_wait_timeout"] == 1
    assert s["limit_errors"]["no_more_connections"] == 1


def write_synthetic_logs(tmp_path):
    """SYNTHETIC per-transaction logs: 2 threads, 2 scripts, failed and skipped lines."""
    rng = np.random.default_rng(1)
    lines0, lines1 = [], []
    vals = {0: [], 1: []}
    for i in range(2000):
        script = i % 2
        us = int(rng.lognormal(7, 0.5))
        vals[script].append(us)
        (lines0 if i % 3 else lines1).append(
            f"{i % 7} {i} {us} {script} {1700000000 + i // 100} {i}"
        )
    lines0.append("1 9999 failed 0 1700000020 5")
    lines1.append("2 9998 deadlock 1 1700000021 6")
    lines1.append("2 9997 skipped 1 1700000021 7")
    (tmp_path / "SYNTHETIC_log.1").write_text("\n".join(lines0) + "\n")
    (tmp_path / "SYNTHETIC_log.1.1").write_text("\n".join(lines1) + "\n")
    return [tmp_path / "SYNTHETIC_log.1", tmp_path / "SYNTHETIC_log.1.1"], vals


def test_parse_logs_threads_scripts_failures(tmp_path):
    files, vals = write_synthetic_logs(tmp_path)
    res = parse_logs(files)
    allv = np.array(vals[0] + vals[1]) / 1000
    assert res.overall.n_ok == 2000
    assert res.overall.n_failed == 2 and res.overall.n_skipped == 1
    assert res.failures_by_type == {"failed": 1, "deadlock": 1}
    assert res.overall.p95_ms == pytest.approx(np.percentile(allv, 95))
    assert res.per_script[0].n_ok == 1000 and res.per_script[1].n_failed == 1
    assert res.per_script[1].p99_ms == pytest.approx(np.percentile(np.array(vals[1]) / 1000, 99))


def test_histogram_percentile_close_to_exact(tmp_path):
    files, vals = write_synthetic_logs(tmp_path)
    res = parse_logs(files)
    counts = res.histogram[0] + res.histogram[1]
    exact = np.percentile(np.array(vals[0] + vals[1]), 95)
    assert percentile_from_hist(counts, 95) == pytest.approx(exact, rel=0.015)


def test_workload_files_exist():
    for f in ("r2_write.sql", "r5/autosave.sql", "sql/r5_seed.sql", "sql/r5_invariants.sql"):
        assert (REPO_ROOT / "workloads" / f).is_file()
