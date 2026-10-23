"""Prometheus export (Tabel 3.8), CPU-saturation flags (Subbab 3.8) and memory per run."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import yaml

from playground.config import REPO_ROOT

SERIES_FILE = REPO_ROOT / "config" / "prometheus" / "series.yaml"


def load_series(path: Path = SERIES_FILE) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def available(url: str) -> bool:
    try:
        return requests.get(f"{url}/-/ready", timeout=3).status_code == 200
    except requests.RequestException:
        return False


def query_range(url: str, expr: str, start: float, end: float, step: float) -> list[dict]:
    resp = requests.get(
        f"{url}/api/v1/query_range",
        params={"query": expr, "start": start, "end": end, "step": step},
        timeout=60,
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("status") != "success":
        raise RuntimeError(f"prometheus error for {expr}: {body}")
    return body["data"]["result"]


def export_window(url: str, dbname: str, start: float, end: float, out: Path) -> pd.DataFrame:
    """All configured series for [start, end] as one long table, saved as Parquet."""
    spec = load_series()
    step = spec["step_s"]
    records: list[dict[str, Any]] = []
    for name, s in spec["series"].items():
        expr = s["expr"].replace("{db}", dbname)
        try:
            result = query_range(url, expr, start, end, step)
        except (requests.RequestException, RuntimeError) as exc:
            records.append(
                {"series": name, "labels": "{}", "ts": None, "value": None, "error": str(exc)[:200]}
            )
            continue
        for item in result:
            labels = json.dumps(item.get("metric", {}), sort_keys=True)
            for ts, val in item.get("values", []):
                records.append(
                    {
                        "series": name,
                        "labels": labels,
                        "ts": float(ts),
                        "value": float(val),
                        "error": None,
                    }
                )
    df = pd.DataFrame.from_records(records, columns=["series", "labels", "ts", "value", "error"])
    df.to_parquet(out, index=False)
    return df


def container_cpu(df: pd.DataFrame, container: str) -> dict[str, float | None]:
    """Mean and max CPU (in cores) of one container over the run window."""
    sel = df[
        (df["series"] == "cpu_cores_by_container")
        & df["labels"].fillna("").str.contains(f'"name": "{container}"')
    ]
    vals = sel["value"].dropna()
    if vals.empty:
        return {"mean": None, "max": None}
    return {"mean": float(vals.mean()), "max": float(vals.max())}


def container_mean(df: pd.DataFrame, series: str, container: str) -> float | None:
    """Mean of one per-container series (for example RSS memory) over the run window."""
    sel = df[
        (df["series"] == series) & df["labels"].fillna("").str.contains(f'"name": "{container}"')
    ]
    vals = sel["value"].dropna()
    return float(vals.mean()) if not vals.empty else None


def series_mean(df: pd.DataFrame, series: str) -> float | None:
    """Mean of one exported series over the run window (all label sets summed per step)."""
    sel = df[df["series"] == series].dropna(subset=["value"])
    if sel.empty:
        return None
    by_step = sel.groupby("ts")["value"].sum() if "ts" in sel else sel["value"]
    return float(by_step.mean())


def check_metrics(url: str, dbname: str) -> list[dict[str, Any]]:
    """Instant query for every configured series; used to validate exporter names (M4)."""
    spec = load_series()
    out = []
    for name, s in spec["series"].items():
        expr = s["expr"].replace("{db}", dbname)
        try:
            resp = requests.get(f"{url}/api/v1/query", params={"query": expr}, timeout=15)
            res = resp.json().get("data", {}).get("result", [])
            out.append(
                {
                    "series": name,
                    "required": s.get("required", False),
                    "present": bool(res),
                    "n": len(res),
                    "expr": expr,
                }
            )
        except requests.RequestException as exc:
            out.append(
                {
                    "series": name,
                    "required": s.get("required", False),
                    "present": False,
                    "n": 0,
                    "expr": expr,
                    "error": str(exc),
                }
            )
    return out


def annotate(
    grafana_url: str,
    password: str,
    text: str,
    tags: list[str],
    time_ms: int,
    time_end_ms: int | None = None,
) -> None:
    """Best-effort Grafana annotation (live monitoring only; never read by the analysis)."""
    body: dict[str, Any] = {
        "dashboardUID": "playground-live",
        "time": time_ms,
        "tags": tags,
        "text": text,
    }
    if time_end_ms:
        body["timeEnd"] = time_end_ms
    try:
        requests.post(
            f"{grafana_url}/api/annotations", json=body, auth=("admin", password), timeout=5
        )
    except requests.RequestException:
        pass
