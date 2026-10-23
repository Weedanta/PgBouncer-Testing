"""Tiny KEY=VALUE env-file reader/writer (the format docker compose --env-file reads)."""

from __future__ import annotations

from pathlib import Path


def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip('"')
    return out


def write_env(path: Path, values: dict[str, object], header: str = "") -> None:
    lines = [f"# {h}" for h in header.splitlines()] if header else []
    lines += [f"{k}={v}" for k, v in values.items()]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
