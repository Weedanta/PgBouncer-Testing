"""Command-line entry point: `playground <command>`."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from playground.config import DEFAULT_EXPERIMENT, ConfigError, load_experiment, measured_runs

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()


@app.callback()
def main() -> None:
    """PgBouncer impact playground (skripsi Weda)."""


@app.command()
def validate(experiment: Path = DEFAULT_EXPERIMENT) -> None:
    """Validate experiment.yaml against the locked research design."""
    try:
        cfg = load_experiment(experiment)
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    d = cfg["design"]
    table = Table(title="Research design", show_header=False)
    table.add_row("paths", ", ".join(d["paths"]))
    table.add_row("profiles", ", ".join(d["profiles"]))
    table.add_row("concurrency", ", ".join(map(str, d["concurrency"])))
    table.add_row("rounds", str(d["rounds"]))
    table.add_row("warmup / measure", f"{d['warmup_s']} s / {d['measure_s']} s")
    table.add_row("measured runs", str(measured_runs(cfg)))
    table.add_row("server", f"{cfg['server']['vcpus']} vCPU, {cfg['server']['memory_mb']} MB")
    table.add_row("pgbench scale", str(cfg["pgbench"]["scale"]))
    console.print(table)
    console.print("[green]experiment.yaml OK[/green]")


if __name__ == "__main__":
    app()
