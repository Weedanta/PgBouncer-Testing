import copy

import pytest

from playground.config import (
    REPO_ROOT,
    ConfigError,
    load_experiment,
    measured_runs,
    needs_reset,
    results_dir,
    validate,
)


@pytest.fixture(scope="module")
def cfg():
    return load_experiment()


def test_experiment_yaml_is_valid(cfg):
    assert validate(cfg) == []


def test_design_matches_proposal(cfg):
    """Tabel 3.1 and 3.7 of the proposal (v5), literally."""
    d = cfg["design"]
    assert d["paths"] == ["direct", "pgbouncer"]
    assert d["concurrency"] == [1, 10, 100, 500, 1000]
    assert d["high_concurrency"] == [500, 1000]
    assert d["rounds"] == 3
    assert (d["warmup_s"], d["measure_s"]) == (10, 30)
    assert "analysis" not in cfg  # statistics are done outside this repo
    assert cfg["calibration"]["concurrency"] == [100, 1000]
    assert cfg["calibration"]["duration_s"] == 30
    assert d["randomize_path_order"] is True
    assert cfg["pgbench"]["protocol"] == "prepared"
    assert cfg["postgres"]["max_connections"] == 1100
    assert cfg["datasets"]["campaign"]["scale"] == 100  # Tabel 3.7 v4
    assert cfg["pgbouncer"]["effective_spindles"] == 0  # dataset in memory (Subbab 3.3.1)
    pgb = cfg["pgbouncer"]
    assert pgb["pool_mode"] == "transaction"
    assert pgb["max_client_conn"] == 1100
    assert pgb["reserve_pool_size"] == 0
    assert pgb["query_wait_timeout"] == 120
    assert pgb["max_prepared_statements"] == 200


def test_proposal_defaults_for_optional_deviations(cfg):
    """DEV-001 and DEV-003..007 exist as switches but are OFF by default (proposal text)."""
    assert cfg["resources"]["layout"] == "dedicated"
    assert cfg["reset"]["policy"] == "write_profiles_only"
    assert cfg["reset"]["prewarm"] is False
    assert cfg["design"]["shuffle_cells_within_round"] is False


def test_reset_only_for_write_profiles(cfg):
    assert not needs_reset(cfg, "R1")
    assert all(needs_reset(cfg, p) for p in ("R2", "R3", "R4", "R5"))
    every = copy.deepcopy(cfg)
    every["reset"]["policy"] = "every_run"
    assert needs_reset(every, "R1")


def test_total_measured_runs_is_150(cfg):
    assert measured_runs(cfg) == 150


def test_campaign_fits_in_about_three_hours(cfg):
    """Weda's request (23 Oct 2026): the campaign takes 2 to 3 hours, not 70."""
    d = cfg["design"]
    per_run_s = d["warmup_s"] + d["measure_s"] + 20  # 20 s reset, snapshots and parsing
    assert measured_runs(cfg) * per_run_s / 3600 <= 3.0


def test_fewer_than_five_levels_is_rejected(cfg):
    bad = copy.deepcopy(cfg)
    bad["design"]["concurrency"] = [100, 200, 500, 1000]
    assert any("at least 5 levels" in e for e in validate(bad))


def test_one_round_is_rejected(cfg):
    bad = copy.deepcopy(cfg)
    bad["design"]["rounds"] = 1
    assert any("design.rounds" in e for e in validate(bad))


def test_r5_weights_sum_to_100(cfg):
    assert sum(cfg["profiles"]["R5"]["scripts"].values()) == 100


def test_wrong_r5_weight_is_rejected(cfg):
    bad = copy.deepcopy(cfg)
    bad["profiles"]["R5"]["scripts"]["workloads/r5/autosave.sql"] = 34
    errors = validate(bad)
    assert any("Tabel 3.6" in e for e in errors)
    assert any("sum to 100" in e for e in errors)


@pytest.mark.parametrize(
    ("section", "key", "value", "fragment"),
    [
        ("pgbouncer", "pool_mode", "session", "pool_mode"),
        ("pgbouncer", "reserve_pool_size", 5, "reserve_pool_size"),
        ("pgbouncer", "query_wait_timeout", 30, "query_wait_timeout"),
        ("postgres", "max_connections", 1000, "max_connections"),
        ("reset", "policy", "never", "reset.policy"),
        ("pgbench", "protocol", "simple", "-M prepared"),
        ("pgbench", "threads", "lots", "pgbench.threads"),
        ("resources", "layout", "laptop", "resources.layout"),
    ],
)
def test_design_violations_are_rejected(cfg, section, key, value, fragment):
    bad = copy.deepcopy(cfg)
    bad[section][key] = value
    assert any(fragment in e for e in validate(bad))


def test_high_concurrency_must_be_subset(cfg):
    bad = copy.deepcopy(cfg)
    bad["design"]["high_concurrency"] = [500, 2000]
    assert any("high_concurrency" in e for e in validate(bad))


def test_smoke_uses_its_own_database(cfg):
    bad = copy.deepcopy(cfg)
    bad["datasets"]["smoke"]["database"] = bad["datasets"]["campaign"]["database"]
    assert any("own database" in e for e in validate(bad))


def test_load_experiment_raises_on_invalid(tmp_path, cfg):
    import yaml

    bad = copy.deepcopy(cfg)
    bad["design"]["paths"] = ["pgbouncer", "direct"]
    path = tmp_path / "experiment.yaml"
    path.write_text(yaml.safe_dump(bad), encoding="utf-8")
    with pytest.raises(ConfigError, match="design.paths"):
        load_experiment(path)


def test_tests_never_point_at_real_results():
    assert results_dir() != REPO_ROOT / "results"
