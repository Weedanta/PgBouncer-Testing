import copy

import pytest

from playground.config import (
    REPO_ROOT,
    ConfigError,
    load_experiment,
    measured_runs,
    results_dir,
    validate,
)


@pytest.fixture(scope="module")
def cfg():
    return load_experiment()


def test_experiment_yaml_is_valid(cfg):
    assert validate(cfg) == []


def test_design_matches_proposal(cfg):
    d = cfg["design"]
    assert d["paths"] == ["direct", "pgbouncer"]
    assert d["concurrency"] == [100, 200, 500, 1000]
    assert d["high_concurrency"] == [500, 1000]
    assert d["rounds"] == 10
    assert (d["warmup_s"], d["measure_s"]) == (60, 300)
    assert cfg["pgbench"]["protocol"] == "prepared"
    assert cfg["postgres"]["max_connections"] == 1100
    assert cfg["pgbouncer"]["max_client_conn"] == 1100


def test_total_measured_runs_is_400(cfg):
    assert measured_runs(cfg) == 400


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
        ("postgres", "max_connections", 1000, "max_connections"),
        ("reset", "policy", "never", "reset.policy"),
        ("analysis", "effect_size_n", "rounds", "effect_size_n"),
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
