import pytest

from playground.config import REPO_ROOT, load_experiment
from playground.lint import lint_experiment, lint_text, statements


def test_all_workloads_pass_the_transaction_mode_criterion():
    results = [
        r
        for r in lint_experiment(load_experiment(), REPO_ROOT)
        if not r.name.startswith("builtin:")
    ]
    assert results and all(r.ok for r in results), [r.findings for r in results]


@pytest.mark.parametrize(
    ("sql", "rule"),
    [
        ("SET statement_timeout = '5s';", "set_session"),
        ("SET SESSION work_mem = '64MB';", "set_session"),
        ("RESET ALL;", "reset"),
        ("LISTEN hasil;", "listen"),
        ("UNLISTEN *;", "listen"),
        ("PREPARE q AS SELECT 1;", "sql_prepare"),
        ("EXECUTE q;", "sql_execute"),
        ("DEALLOCATE q;", "sql_deallocate"),
        ("DECLARE c CURSOR WITH HOLD FOR SELECT 1;", "cursor_with_hold"),
        ("CREATE TEMP TABLE t (x int);", "temp_table_preserve"),
        ("CREATE TEMPORARY TABLE t (x int) ON COMMIT DELETE ROWS;", "temp_table_preserve"),
        ("LOAD 'auto_explain';", "load"),
        ("SELECT pg_advisory_lock(1);", "session_advisory_lock"),
        ("SELECT pg_try_advisory_lock(1, 2);", "session_advisory_lock"),
        ("SELECT pg_advisory_unlock_all();", "session_advisory_lock"),
        ("SELECT set_config('work_mem', '8MB', false);", "set_config_session"),
    ],
)
def test_unsafe_constructs_are_detected(sql, rule):
    res = lint_text("synthetic", sql)
    assert rule in {f.rule for f in res.findings}


@pytest.mark.parametrize(
    "sql",
    [
        "BEGIN; SET LOCAL statement_timeout = '30s'; SELECT 1; END;",
        "UPDATE t SET x = 1 WHERE id = 2;",
        "INSERT INTO t VALUES (1) ON CONFLICT (id) DO UPDATE SET x = EXCLUDED.x;",
        "SELECT pg_advisory_xact_lock(7305, 1);",
        "CREATE TEMP TABLE t (x int) ON COMMIT DROP;",
        "DECLARE c CURSOR FOR SELECT 1;",
        "NOTIFY hasil;",
        "-- SET work_mem = '1GB' in a comment is fine\nSELECT 'LISTEN in a string';",
        "SELECT set_config('work_mem', '8MB', true);",
    ],
)
def test_safe_constructs_pass(sql):
    assert lint_text("synthetic", sql).ok


def test_meta_commands_and_gset_are_ignored():
    text = (
        "\\set k random(1, 5)\nUPDATE c SET n = n + 1 WHERE k = :k RETURNING n \\gset\nSELECT :n;"
    )
    assert statements(text) == ["UPDATE c SET n = n + 1 WHERE k = :k RETURNING n", "SELECT :n"]
