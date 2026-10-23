#!/usr/bin/env python3
"""Fail if any workload uses a session-level feature unsafe in transaction mode (Tabel 2.2).

Usage: python tools/lint_session_features.py [--report PATH]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playground.config import REPO_ROOT, load_experiment  # noqa: E402
from playground.lint import lint_experiment, report_markdown  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", type=Path, help="write the Markdown report here")
    ap.add_argument(
        "--allow-unchecked",
        action="store_true",
        help="do not fail when pgbench is missing for built-in scripts",
    )
    args = ap.parse_args()
    results = lint_experiment(load_experiment(), REPO_ROOT)
    text = report_markdown(results)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding="utf-8")
    print(text)
    bad = [
        r
        for r in results
        for f in r.findings
        if not (args.allow_unchecked and f.rule == "unchecked")
    ]
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
