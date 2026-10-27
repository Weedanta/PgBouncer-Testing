"""Command-line entry point: `playground <command>` (usually called through `make`).

Host-side commands (run with `uv run playground ...` on the Linux/WSL host):
  validate, probe, secrets, render, lint
Client-container commands (run with `docker compose exec client playground ...`):
  init-data, check-metrics, smoke, calibrate, pool-sweep, adjust, lock, plan, campaign, resume,
  status, evidence
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from playground.config import (
    DEFAULT_EXPERIMENT,
    REPO_ROOT,
    ConfigError,
    load_experiment,
    measured_runs,
    results_dir,
)

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()
HOST_ENV = REPO_ROOT / "config" / "host.env"


def _cfg(experiment: Path = DEFAULT_EXPERIMENT) -> dict:
    try:
        return load_experiment(experiment)
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


def _host_env() -> dict[str, str]:
    from playground.envfile import read_env

    if not HOST_ENV.exists():
        console.print("[red]config/host.env not found: run `make probe` first[/red]")
        raise typer.Exit(1)
    return read_env(HOST_ENV)


@app.callback()
def main() -> None:
    """PgBouncer impact playground (skripsi Weda)."""


# ---------------------------------------------------------------------------
# host side
# ---------------------------------------------------------------------------
@app.command()
def validate(experiment: Path = DEFAULT_EXPERIMENT) -> None:
    """Validate experiment.yaml against the research design."""
    cfg = _cfg(experiment)
    d = cfg["design"]
    table = Table(title="Research design (proposal v5)", show_header=False)
    table.add_row("paths", ", ".join(d["paths"]))
    table.add_row("profiles", ", ".join(d["profiles"]))
    table.add_row("concurrency", ", ".join(map(str, d["concurrency"])))
    table.add_row("rounds", str(d["rounds"]))
    table.add_row("warmup / measure", f"{d['warmup_s']} s / {d['measure_s']} s")
    table.add_row("measured runs", str(measured_runs(cfg)))
    table.add_row("pool_mode", cfg["pgbouncer"]["pool_mode"])
    table.add_row("resource layout", cfg["resources"]["layout"])
    table.add_row("pgbench scale", str(cfg["datasets"]["campaign"]["scale"]))
    table.add_row("reset policy", cfg["reset"]["policy"])
    table.add_row("shuffle cells", str(d["shuffle_cells_within_round"]))
    console.print(table)
    console.print("[green]experiment.yaml OK[/green]")


@app.command()
def probe(
    pgdata: Path = typer.Option(
        Path.home() / "pgbouncer-playground" / "pgdata",
        help="host directory for the PostgreSQL data (local disk)",
    ),
    results: Path = typer.Option(REPO_ROOT / "results", help="host directory for results"),
) -> None:
    """Inspect the host and write config/host.env + docs/HOST.md."""
    from playground import hostprobe
    from playground.envfile import write_env

    cfg = _cfg()
    cores = hostprobe.read_topology()
    mem = hostprobe.meminfo_mb()
    try:
        plan = hostprobe.plan_resources(cores, mem, cfg["resources"])
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    plan["HOST_UID"] = os.getuid()
    plan["HOST_GID"] = os.getgid()
    plan["PGDATA_HOST"] = str(pgdata)
    plan["RESULTS_HOST"] = str(results)
    pgdata.mkdir(parents=True, exist_ok=True)
    results.mkdir(parents=True, exist_ok=True)
    write_env(HOST_ENV, plan, header="Written by `make probe`. Rerun the probe instead of editing.")
    warn = hostprobe.warnings(cores, mem, cfg, mem_postgres_mb=int(plan["MEM_POSTGRES_MB"]))
    facts = hostprobe.host_facts()
    (REPO_ROOT / "docs" / "HOST.md").write_text(
        hostprobe.host_md(plan, facts, cores, mem, warn), encoding="utf-8"
    )
    for k, v in plan.items():
        console.print(f"  {k}={v}")
    for w in warn:
        console.print(f"[yellow]warning: {w}[/yellow]")
    console.print("[green]wrote config/host.env and docs/HOST.md[/green]")


@app.command()
def secrets(force: bool = False) -> None:
    """Generate local secrets into secrets/ (gitignored)."""
    from playground.secretgen import generate

    created = generate(force=force)
    console.print(f"created: {created or 'nothing (already present)'}")


@app.command()
def render() -> None:
    """Render config/active/{postgresql.conf,pg_hba.conf,pgbouncer.ini}."""
    from playground.envfile import read_env
    from playground.render import render_all

    cfg = _cfg()
    versions = read_env(REPO_ROOT / "config" / "versions.env")
    summary = render_all(cfg, _host_env(), subnet=versions.get("PGNET_SUBNET", "172.29.0.0/24"))
    console.print_json(json.dumps({k: v for k, v in summary.items() if k != "host_env"}))


@app.command()
def lint(report: Path | None = None) -> None:
    """Session-feature linter (Tabel 2.2) over every workload script."""
    from playground.lint import lint_experiment, report_markdown

    results = lint_experiment(_cfg(), REPO_ROOT)
    text = report_markdown(results)
    if report:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(text, encoding="utf-8")
    console.print(text)
    if not all(r.ok for r in results):
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# client container
# ---------------------------------------------------------------------------
def _ep():
    from playground.settings import endpoints

    return endpoints()


@app.command("init-data")
def init_data(smoke: bool = typer.Option(False, help="build the small smoke dataset")) -> None:
    """pgbench init + R5 schema/seed + template (Subbab 3.4)."""
    from playground.data import init_data as run

    run(_cfg(), _ep(), "smoke" if smoke else "campaign")


@app.command("check-metrics")
def check_metrics(database: str = "bench") -> None:
    """Verify that every Prometheus series of Tabel 3.8 exists (run during a test load)."""
    from playground.prom import check_metrics as run

    rows = run(_ep().prometheus_url, database)
    table = Table("series", "required", "present", "n")
    bad = 0
    for r in rows:
        ok = r["present"] or not r["required"]
        bad += not ok
        table.add_row(
            r["series"],
            str(r["required"]),
            "[green]yes[/green]" if r["present"] else "[red]no[/red]",
            str(r["n"]),
        )
    console.print(table)
    if bad:
        console.print("[red]missing required series: fix config/prometheus/series.yaml[/red]")
        raise typer.Exit(1)


def _campaign(campaign_id: str, smoke: bool):
    from playground.runner import Campaign

    return Campaign(_cfg(), _ep(), campaign_id, smoke)


@app.command()
def smoke() -> None:
    """All runner steps on the small smoke dataset (labeled SMOKE, never used in the thesis)."""
    from playground.data import dataset_ready
    from playground.data import init_data as run_init

    cfg = _cfg()
    ep = _ep()
    if not dataset_ready(ep, cfg, "smoke"):
        run_init(cfg, ep, "smoke")
    cid = "SMOKE-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    c = _campaign(cid, smoke=True)
    rows = c.create()
    console.print(
        f"smoke campaign {cid}: {len(rows)} runs, about {c.expected_hours(len(rows)) * 60:.0f} min"
    )
    problems = c.full_preflight()
    for p in problems:
        console.print(f"[yellow]preflight: {p}[/yellow]")
    t0 = time.monotonic()
    c.run()
    minutes = (time.monotonic() - t0) / 60
    console.print(
        f"smoke runs finished in {minutes:.1f} min (limit {cfg['smoke']['max_minutes']} min)"
    )
    console.print_json(json.dumps(c.status()))


@app.command()
def calibrate(
    quick: bool = typer.Option(False, help="10 s runs, 1 repetition (pipeline test only)"),
    smoke_dataset: bool = typer.Option(
        False, "--smoke-dataset", help="use the smoke dataset (pipeline test only)"
    ),
) -> None:
    """RM1 calibration: step A + four checks of the 'pengamatan awal' (Subbab 3.3)."""
    from playground.calibrate import run_calibration
    from playground.envfile import read_env

    host_env = (
        read_env(HOST_ENV)
        if HOST_ENV.exists()
        else {"POSTGRES_PHYSICAL_CORES": os.environ.get("PLAYGROUND_POSTGRES_PHYSICAL_CORES", "1")}
    )
    out = run_calibration(
        _cfg(), _ep(), host_env, "smoke" if smoke_dataset else "campaign", quick=quick
    )
    console.print(f"report: {out / 'REPORT.md'}")


@app.command("pool-sweep")
def pool_sweep(
    smoke_dataset: bool = typer.Option(
        False, "--smoke-dataset", help="use the smoke dataset (pipeline test only)"
    ),
) -> None:
    """DEV-022: find the single default_pool_size adjustment (P doubled, D3 criterion)."""
    from playground.envfile import read_env
    from playground.poolsweep import run_pool_sweep

    if not HOST_ENV.exists():
        console.print("[red]config/host.env missing: run `make probe` first[/red]")
        raise typer.Exit(1)
    out = run_pool_sweep(
        _cfg(), _ep(), read_env(HOST_ENV), "smoke" if smoke_dataset else "campaign"
    )
    console.print((out / "REPORT.md").read_text(encoding="utf-8"))


@app.command()
def memcheck(
    smoke_dataset: bool = typer.Option(
        False, "--smoke-dataset", help="use the smoke dataset (pipeline test only)"
    ),
) -> None:
    """DEV-019: memory limits under the heaviest load (R3, highest c, both paths)."""
    from playground.envfile import read_env
    from playground.memcheck import run_memcheck

    if not HOST_ENV.exists():
        console.print("[red]config/host.env missing: run `make probe` first[/red]")
        raise typer.Exit(1)
    out = run_memcheck(_cfg(), _ep(), read_env(HOST_ENV), "smoke" if smoke_dataset else "campaign")
    console.print(f"report: {out / 'REPORT.md'}")
    console.print((out / "REPORT.md").read_text(encoding="utf-8"))


@app.command()
def adjust(param: str, value: int, reason: str) -> None:
    """Record the one allowed adjustment of a Tabel 3.2 value (then: make render reload)."""
    from playground.calibrate import adjust as run
    from playground.calibrate import latest_report

    last = latest_report(passed_only=False)
    try:
        entry = run(
            param,
            value,
            reason,
            str(last) if last else None,
            _cfg()["calibration"]["max_adjustments_per_value"],
        )
    except (ValueError, RuntimeError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"[green]recorded[/green] {param} = {entry}")


@app.command("lock")
def lock_cmd(
    allow_without_calibration: bool = typer.Option(
        False, help="only for pipeline tests: lock without a passing calibration"
    ),
) -> None:
    """Freeze config/active + workloads + versions into config/locked/ (Subbab 3.3)."""
    from playground import lock
    from playground.calibrate import latest_report

    report = latest_report(passed_only=True)
    if report is None and not allow_without_calibration:
        console.print("[red]no passing calibration report found (make calibrate)[/red]")
        raise typer.Exit(1)
    info = lock.write_lock(_ep(), report)
    console.print_json(json.dumps({k: v for k, v in info.items() if k != "inputs"}))


@app.command()
def plan(campaign: str) -> None:
    """Write results/<campaign>/plan.csv (150 runs) without running anything."""
    c = _campaign(campaign, smoke=False)
    rows = c.create()
    console.print(f"plan written: {c.plan_path} ({len(rows)} runs)")


@app.command()
def campaign(
    campaign_id: str = typer.Argument(..., help="for example C1-20261101"),
    yes: bool = typer.Option(False, "--yes", help="do not ask for confirmation"),
    max_runs: int | None = typer.Option(None, help="stop after this many runs"),
) -> None:
    """Run the full campaign (asks for confirmation first)."""
    from playground import lock

    c = _campaign(campaign_id, smoke=False)
    if lock.read_lock() is None:
        console.print("[red]configuration is not locked: run calibration and `make lock`[/red]")
        raise typer.Exit(1)
    if not c.plan_path.exists():
        c.create()
    todo = c.remaining()
    problems = c.full_preflight()
    if problems:
        for p in problems:
            console.print(f"[red]preflight: {p}[/red]")
        raise typer.Exit(1)
    console.print(
        f"{len(todo)} runs left; expected about {c.expected_hours(len(todo)):.1f} h "
        "including database resets (estimate)."
    )
    if not yes and not typer.confirm("Start now?"):
        raise typer.Exit(0)
    c.run(max_runs=max_runs)


@app.command()
def resume(
    campaign_id: str, yes: bool = typer.Option(False, "--yes"), max_runs: int | None = None
) -> None:
    """Continue an interrupted campaign from the first run that is not done."""
    info_path = results_dir() / campaign_id / "campaign.json"
    smoke_flag = json.loads(info_path.read_text())["smoke"] if info_path.exists() else False
    c = _campaign(campaign_id, smoke=smoke_flag)
    problems = c.full_preflight()
    if problems:
        for p in problems:
            console.print(f"[red]preflight: {p}[/red]")
        raise typer.Exit(1)
    todo = c.remaining()
    console.print(f"{len(todo)} runs left; about {c.expected_hours(len(todo)):.1f} h")
    if not yes and not typer.confirm("Resume now?"):
        raise typer.Exit(0)
    c.run(max_runs=max_runs)


@app.command()
def status(campaign_id: str) -> None:
    """Progress of a campaign."""
    info_path = results_dir() / campaign_id / "campaign.json"
    smoke_flag = json.loads(info_path.read_text())["smoke"]
    console.print_json(json.dumps(_campaign(campaign_id, smoke_flag).status()))


@app.command()
def evidence(campaign_id: str) -> None:
    """Validation evidence (Subbab 3.2 tahap validasi): pooling and load-generator checks."""
    import pandas as pd

    from playground.runsfile import load_runs

    runs = load_runs(results_dir() / campaign_id)
    top = runs["concurrency"].max()
    d = runs[(runs["path"] == "direct") & (runs["concurrency"] == top)]
    b = runs[(runs["path"] == "pgbouncer") & (runs["concurrency"] == top)]
    rows = [
        (
            "direct path holds ~c backends at max c",
            f"max backends {d['pg_backends_max'].max():.0f} (c={top})",
        ),
        ("pgbouncer: server connections <= pool", f"max sv total {b['sv_total_max'].max():.0f}"),
        (
            "pgbouncer: clients wait in the queue (cl_waiting > 0)",
            f"max cl_waiting {b['cl_waiting_max'].max():.0f}",
        ),
        (
            "client CPU saturated (runs)",
            str(int(runs["flag_client_cpu_saturated"].fillna(False).astype(bool).sum())),
        ),
        (
            "checkpoint inside a measurement (runs)",
            str(int(runs["flag_checkpoint_during_run"].fillna(False).astype(bool).sum())),
        ),
        (
            "failed tx / aborted clients (runs)",
            f"{int((runs['failed_tx'] > 0).sum())} / {int((runs['aborted_clients'] > 0).sum())}",
        ),
    ]
    # Subbab 3.5.2 (v4): the dataset stays in memory after warmup, and R3/R4 row-lock
    # contention is visible separately from R1/R2/R5.
    if "disk_read_mb_s_mean" in runs:
        rows.append(
            (
                "dataset in memory: disk reads during measurement (MB/s, median / max)",
                f"{runs['disk_read_mb_s_mean'].median():.2f} / "
                f"{runs['disk_read_mb_s_mean'].max():.2f}",
            )
        )
    if "cache_hit_ratio" in runs:
        rows.append(
            (
                "shared_buffers hit ratio (median / min)",
                f"{runs['cache_hit_ratio'].median():.4f} / {runs['cache_hit_ratio'].min():.4f}",
            )
        )
    if "lock_waiting_mean" in runs:
        top_runs = runs[runs["concurrency"] == top]
        tpcb = top_runs["profile"].isin(["R3", "R4"])
        rows.append(
            (
                f"sessions waiting for a lock at c={top} (mean): R3/R4 vs R1/R2/R5",
                f"{top_runs.loc[tpcb, 'lock_waiting_mean'].mean():.1f} vs "
                f"{top_runs.loc[~tpcb, 'lock_waiting_mean'].mean():.1f}",
            )
        )
    table = Table("check", "observed")
    for r in rows:
        table.add_row(*r)
    console.print(table)
    console.print(pd.DataFrame(runs.groupby(["profile", "path"])["client_cpu_mean"].max()))


if __name__ == "__main__":
    app()
