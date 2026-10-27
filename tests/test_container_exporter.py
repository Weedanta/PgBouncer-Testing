"""DEV-020 container exporter: parsing of Docker stats documents (synthetic input)."""

from __future__ import annotations

from playground.container_exporter import metrics_from_stats, parse_started_at, render

V2 = {
    "memory_stats": {
        "usage": 600 * 2**20,
        "stats": {"anon": 400 * 2**20, "inactive_file": 100 * 2**20, "file": 150 * 2**20},
    },
    "cpu_stats": {"cpu_usage": {"total_usage": 12_500_000_000}},
}


def test_cgroup_v2_values():
    m = metrics_from_stats(V2)
    assert m["container_memory_rss"] == 400 * 2**20
    assert m["container_memory_working_set_bytes"] == 500 * 2**20
    assert m["container_memory_usage_bytes"] == 600 * 2**20
    assert m["container_cpu_usage_seconds_total"] == 12.5


def test_cgroup_v1_names():
    m = metrics_from_stats(
        {"memory_stats": {"usage": 1000, "stats": {"rss": 700, "total_inactive_file": 100}}}
    )
    assert m["container_memory_rss"] == 700
    assert m["container_memory_working_set_bytes"] == 900


def test_missing_fields_are_skipped():
    assert metrics_from_stats({}) == {}


def test_started_at_is_utc_with_nanoseconds():
    assert parse_started_at("1970-01-01T00:01:40.123456789Z") == 100.123456
    assert parse_started_at("0001-01-01T00:00:00Z") is None


def test_render_uses_cadvisor_names_and_label():
    text = render({"pgb-postgres": metrics_from_stats(V2)}, ok=True, last=1.0)
    assert 'container_memory_rss{name="pgb-postgres"} 419430400.0' in text
    assert "# TYPE container_cpu_usage_seconds_total counter" in text
    assert "container_exporter_up 1" in text
