import pytest


@pytest.fixture(autouse=True)
def _isolate_results(tmp_path, monkeypatch):
    """Rule 1: nothing under tests/ may ever write to the real results/ directory."""
    monkeypatch.setenv("PLAYGROUND_RESULTS_DIR", str(tmp_path / "results"))
